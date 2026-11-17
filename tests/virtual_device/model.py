"""Deterministic input-peripheral model; executes the candidate's real Buttons.

This is NOT the full device runtime, an ESP32 emulator, or an untrusted-code
sandbox. No application screens, sensor values, or gestures are implemented here.
The model supplies pin levels, scanner ticks, bounded queues, and power epochs.
"""
from __future__ import annotations

import ast
import builtins
from collections import deque
import hashlib
import heapq
from pathlib import Path
from types import ModuleType, SimpleNamespace

MODEL_VERSION = "knowco2-input-v1"
TICKS_PERIOD = 1 << 29
PINS = {"A": ("D0", False), "B": ("D1", True), "C": ("D2", True)}
MAX_ADVANCE_MS = 120_000
MAX_TRACE = 20_000
HOLD_NAMES = {"a_hold_s": "LP_A_HOLD_SECONDS", "b_hold_s": "B_HOLD_SECONDS",
              "c_hold_s": "D2_HOLD_SECONDS", "ab_hold_s": "OTA_UNLOCK_HOLD_SECONDS"}


def read_hold_config(source):
    """Read declared literal constants, never copy them into a simulator fork."""
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in HOLD_NAMES.values():
                    value = ast.literal_eval(node.value)
                    if type(value) not in (float, int) or not 0 < value <= 60:
                        raise ValueError("unsupported hold constant: " + target.id)
                    if target.id in values:
                        raise ValueError("duplicate hold constant: " + target.id)
                    values[target.id] = value
    if set(values) != set(HOLD_NAMES.values()):
        raise ValueError("missing candidate hold constants")
    return {key: values[name] for key, name in HOLD_NAMES.items()}


class Event:
    def __init__(self, key_number=0, pressed=False, timestamp=0):
        self.key_number = key_number
        self.pressed = pressed
        self.timestamp = timestamp


class EventQueue:
    """CP 10.2.1 Keys.c/EventQueue.c: keep old events, reject new on overflow."""
    def __init__(self, capacity):
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise ValueError("unsupported queue capacity")
        self.capacity = capacity
        self.items = deque()
        self.overflowed = False
        self.high_water = 0
        self.lost = 0

    def append(self, event):
        if len(self.items) == self.capacity:
            self.overflowed = True
            self.lost += 1
            return
        self.items.append(event)
        self.high_water = max(self.high_water, len(self.items))

    def get_into(self, target):
        if not self.items:
            return False
        target.key_number, target.pressed, target.timestamp = self.items.popleft()
        return True

    def clear(self):
        self.items.clear()
        self.overflowed = False


class Scanner:
    def __init__(self, world, pins, *, value_when_pressed, pull=True,
                 interval=0.02, max_events=64, debounce_threshold=1):
        if debounce_threshold != 1 or not pull:
            raise NotImplementedError("only pulled Keys with debounce_threshold=1 are qualified")
        self.world = world
        self.pins = tuple(pins)
        if any(pin not in world.levels for pin in self.pins):
            raise ValueError("unknown board pin")
        self.interval_ms = round(interval * 1000)
        if not 1 <= self.interval_ms <= 1000:
            raise ValueError("unsupported scan interval")
        self.value_when_pressed = value_when_pressed
        self.previous = [False] * len(self.pins)
        self.events = EventQueue(max_events)
        self.next_scan = world.now_ms + self.interval_ms
        self.active = True
        world.scanners.append(self)

    def scan(self):
        for key, pin in enumerate(self.pins):
            pressed = self.world.levels[pin] == self.value_when_pressed
            if pressed != self.previous[key]:
                self.previous[key] = pressed
                ts = self.world.ticks_ms()
                self.events.append((key, pressed, ts))
                self.world.record("scanner", pin=pin, pressed=pressed, timestamp=ts)
        self.next_scan += self.interval_ms

    def deinit(self):
        self.active = False
        self.events.clear()


class InputDevice:
    """Independent module namespace and clock per simulated device.

    Screen context is a declared fixture input, NOT simulated application state.
    The future full-runtime adapter must obtain these guards from real state.py.
    """
    def __init__(self, root: Path, *, context="main", tick_offset=0):
        self.root = Path(root).resolve()
        self.source = (self.root / "knowco2/buttons.py").read_bytes()
        self.source_sha256 = hashlib.sha256(self.source).hexdigest()
        config_source = (self.root / "knowco2/config.py").read_bytes()
        self.config_sha256 = hashlib.sha256(config_source).hexdigest()
        self.hold_config = read_hold_config(config_source)
        self.now_ms = 0
        self.boot_ms = 0
        self.tick_offset = tick_offset
        self.epoch = 0
        self.powered = False
        self.context = context
        if context not in ("main", "info"):
            raise ValueError("context must be main or info")
        if type(tick_offset) is not int or not 0 <= tick_offset < TICKS_PERIOD:
            raise ValueError("invalid tick offset")
        self.levels = {pin: not active for pin, active in PINS.values()}
        self.scanners = []
        self.pending = []
        self.sequence = 0
        self.trace = []
        self.delivered = []
        self.buttons = None
        self.module = None
        self.next_poll = 0

    def record(self, kind, **fields):
        if len(self.trace) >= MAX_TRACE:
            raise RuntimeError("trace limit reached; shorten or split scenario")
        self.trace.append({"at_ms": self.now_ms, "epoch": self.epoch, "kind": kind, **fields})

    def ticks_ms(self):
        return (self.now_ms - self.boot_ms + self.tick_offset) % TICKS_PERIOD

    def plug(self):
        if self.powered:
            raise ValueError("device already powered")
        if any(self.levels[pin] == active for pin, active in PINS.values()):
            raise NotImplementedError("held-at-boot scan semantics require separate qualification")
        self.epoch += 1
        self.boot_ms = self.now_ms
        self.powered = True
        self.scanners = []
        keypad = SimpleNamespace(
            Keys=lambda *a, **k: Scanner(self, *a, **k), Event=Event)
        imports = {
            "keypad": keypad,
            "supervisor": SimpleNamespace(ticks_ms=self.ticks_ms),
            "time": SimpleNamespace(monotonic=lambda: (self.now_ms - self.boot_ms) / 1000),
        }

        def import_adapter(name, globals=None, locals=None, fromlist=(), level=0):
            if level or name not in imports:
                raise ImportError("unmodeled input-runtime import: " + name)
            return imports[name]

        module = ModuleType("candidate_buttons")
        module.__file__ = str(self.root / "knowco2/buttons.py")
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": import_adapter}
        # Exact source bytes; no AST extraction, source edits, or copied gestures.
        exec(compile(self.source, module.__file__, "exec"), module.__dict__)
        self.module = module
        self.buttons = module.Buttons(
            "D0", "D1", "D2", **self.hold_config,
            b_hold_allowed=lambda: self.context == "info",
            b_fire_on_press=lambda: self.context == "main" and not self.buttons.is_down(module.A),
        )
        if self.buttons.backend != "keypad":
            raise RuntimeError("candidate did not select the hardware-scanner backend")
        if len(self.scanners) != 2:
            raise RuntimeError("candidate scanner topology changed; review the model")
        self.next_poll = self.now_ms + 10
        self.record("plug", source_sha256=self.source_sha256)

    def unplug(self):
        if not self.powered:
            raise ValueError("device already unpowered")
        # Deliberately no firmware deinit, shutdown, flush, or final poll.
        self.powered = False
        self.scanners = []
        self.buttons = None
        self.module = None
        self.record("unplug")

    def set_button(self, button: str, pressed: bool):
        if button not in PINS or type(pressed) is not bool:
            raise ValueError("expected A/B/C and boolean pressed")
        pin, active = PINS[button]
        self.levels[pin] = active if pressed else not active
        self.record("pin", button=button, pin=pin, value=self.levels[pin])

    def schedule_button(self, at_ms, button, pressed):
        if type(at_ms) is not int or at_ms < self.now_ms:
            raise ValueError("events must be scheduled at or after current world time")
        if button not in PINS or type(pressed) is not bool:
            raise ValueError("invalid button event")
        self.sequence += 1
        heapq.heappush(self.pending, (at_ms, self.sequence, button, pressed))

    def poll(self):
        if not self.powered:
            return []
        actions = self.buttons.poll()
        for action in actions:
            entry = {"at_ms": self.now_ms, "epoch": self.epoch, "action": action}
            self.delivered.append(entry)
            self.record("action", action=action)
        return actions

    def advance(self, duration_ms, *, service=True):
        if type(duration_ms) is not int or not 0 <= duration_ms <= MAX_ADVANCE_MS:
            raise ValueError("advance must be integer milliseconds in 0..120000")
        if type(service) is not bool:
            raise ValueError("service must be boolean")
        target = self.now_ms + duration_ms
        if service and self.next_poll <= self.now_ms:
            self.next_poll = self.now_ms + 10
        # Physical transitions precede scans at equal timestamps. Scans run even
        # when the application is blocked (service=False). No real sleeps.
        while True:
            times = [target]
            if self.pending and self.pending[0][0] <= target:
                times.append(self.pending[0][0])
            if self.powered:
                times.extend(s.next_scan for s in self.scanners if s.active)
                if service:
                    times.append(self.next_poll)
            self.now_ms = min(times)
            while self.pending and self.pending[0][0] == self.now_ms:
                _, _, button, pressed = heapq.heappop(self.pending)
                self.set_button(button, pressed)
            if self.powered:
                for scanner in self.scanners:
                    if scanner.active and scanner.next_scan == self.now_ms:
                        scanner.scan()
                if service and self.next_poll == self.now_ms:
                    self.poll()
                    self.next_poll += 10
            if self.now_ms == target:
                break
        if not service:
            self.next_poll = self.now_ms + 10

    def action_names(self):
        return [item["action"] for item in self.delivered]


def replay(root: Path, scenario: dict):
    """Strict, data-only replay contract suitable for a later browser adapter."""
    required = {"id", "catalog_id", "title", "steps", "expected_actions"}
    if not required <= scenario.keys() or scenario.keys() - required - {"context", "tick_offset"}:
        raise ValueError("invalid scenario fields")
    device = InputDevice(root, context=scenario.get("context", "main"),
                         tick_offset=scenario.get("tick_offset", 0))
    for step in scenario["steps"]:
        op = step.get("op")
        allowed = {
            "plug": {"op"}, "unplug": {"op"}, "poll": {"op"},
            "button": {"op", "button", "pressed"},
            "advance": {"op", "ms", "service"},
            "schedule": {"op", "at_ms", "button", "pressed"},
        }
        if op not in allowed or set(step) - allowed[op]:
            raise ValueError("unknown operation or fields: " + repr(step))
        if op == "plug":
            device.plug()
        elif op == "unplug":
            device.unplug()
        elif op == "poll":
            device.poll()
        elif op == "button":
            device.set_button(step["button"], step["pressed"])
        elif op == "schedule":
            device.schedule_button(step["at_ms"], step["button"], step["pressed"])
        else:
            device.advance(step["ms"], service=step.get("service", True))
    return device
