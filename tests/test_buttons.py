#!/usr/bin/env python3
"""RC-52 button tests: presses are never dropped, holds are measured from
event timestamps, and gesture semantics match RC-51.

Runs the real knowco2.buttons.Buttons against a fake keypad event source
whose clock we control, so we can simulate "the main loop was blocked for
8 s while the user pressed B twice"."""
import sys, types

sys.path.insert(0, "..")

# ---- fake keypad / supervisor -------------------------------------------
CLOCK = {"ms": 100000}

sup = types.ModuleType("supervisor")
sup.ticks_ms = lambda: CLOCK["ms"]
sys.modules["supervisor"] = sup

kp = types.ModuleType("keypad")


class Event:
    def __init__(self, key_number=0, pressed=True, timestamp=0):
        self.key_number, self.pressed, self.timestamp = key_number, pressed, timestamp


class _Queue:
    def __init__(self):
        self._q = []
        self.overflowed = False

    def get_into(self, ev):
        if not self._q:
            return False
        k, p, t = self._q.pop(0)
        ev.key_number, ev.pressed, ev.timestamp = k, p, t
        return True

    def clear(self):
        self._q = []
        self.overflowed = False


REGISTRY = {}


class Keys:
    def __init__(self, pins, value_when_pressed, pull=True, interval=0.02, max_events=64):
        self.pins = pins
        self.events = _Queue()
        for i, p in enumerate(pins):
            REGISTRY[p] = (self, i)

    def deinit(self):
        pass


kp.Keys, kp.Event = Keys, Event
sys.modules["keypad"] = kp

from knowco2 import buttons as bm  # noqa: E402

# ---- harness ----------------------------------------------------------------
PIN_A, PIN_B, PIN_C = "D0", "D1", "D2"
SCREEN = {"main": True, "apinfo": False}

btn = bm.Buttons(PIN_A, PIN_B, PIN_C,
                 a_hold_s=2.0, b_hold_s=2.0, c_hold_s=2.0, ab_hold_s=3.0,
                 b_hold_allowed=lambda: SCREEN["apinfo"],
                 b_fire_on_press=lambda: SCREEN["main"] and not btn.is_down(bm.A))
assert btn.backend == "keypad"
KEYMAP = {bm.A: PIN_A, bm.B: PIN_B, bm.C: PIN_C}


def hw(key, pressed, at_ms=None):
    """Hardware event at absolute time (the scanner timer saw it)."""
    keys, idx = REGISTRY[KEYMAP[key]]
    keys.events._q.append((idx, pressed, CLOCK["ms"] if at_ms is None else at_ms))


def advance(ms):
    CLOCK["ms"] += ms


fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ((" — " + detail) if (detail and not cond) else ""))
    if not cond:
        fails += 1


# T1: the original bug — press+release entirely inside an 8 s blocking call.
hw(bm.B, True, 100200); hw(bm.B, False, 100320)     # user taps B at t+200 ms
advance(8000)                                       # main loop was in session.post()
acts = btn.poll()
check("T1 tap during 8 s block is delivered", acts == [bm.B_SHORT], str(acts))

# T2: two taps during a block → two mode changes, in order, not one.
hw(bm.B, True, CLOCK["ms"] + 100); hw(bm.B, False, CLOCK["ms"] + 200)
hw(bm.B, True, CLOCK["ms"] + 900); hw(bm.B, False, CLOCK["ms"] + 1000)
advance(5000)
acts = btn.poll()
check("T2 two taps during block = two actions", acts == [bm.B_SHORT, bm.B_SHORT], str(acts))

# T3: B on MAIN fires on press (instant), release produces nothing extra.
hw(bm.B, True); acts = btn.poll()
check("T3 B fires on press on MAIN", acts == [bm.B_SHORT], str(acts))
advance(80); hw(bm.B, False); acts = btn.poll()
check("T3 B release adds nothing", acts == [], str(acts))

# T4: C short vs C hold (release-timed), measured by event timestamps even if blocked.
hw(bm.C, True); advance(150); hw(bm.C, False); acts = btn.poll()
check("T4 C short", acts == [bm.C_SHORT], str(acts))
hw(bm.C, True); advance(2100); acts = btn.poll()
check("T4 C hold fires while held at 2 s", acts == [bm.C_HOLD], str(acts))
hw(bm.C, False); acts = btn.poll()
check("T4 C hold release adds nothing", acts == [], str(acts))
t0 = CLOCK["ms"]
hw(bm.C, True, t0); hw(bm.C, False, t0 + 2500)       # full 2.5 s hold happened while blocked
advance(6000); acts = btn.poll()
check("T4 C hold completed during block still = hold", acts == [bm.C_HOLD], str(acts))

# T5: B hold only on APINFO; on MAIN a long B is still (already) a short press.
SCREEN["main"], SCREEN["apinfo"] = False, True
hw(bm.B, True); advance(2100); acts = btn.poll()
check("T5 B hold on APINFO", acts == [bm.B_HOLD], str(acts))
hw(bm.B, False); btn.poll()
hw(bm.B, True); advance(100); hw(bm.B, False); acts = btn.poll()
check("T5 B short on APINFO is release-timed", acts == [bm.B_SHORT], str(acts))
SCREEN["main"], SCREEN["apinfo"] = True, False

# T6: A+B combo — 3 s hold → OTA unlock; no A/B short or hold leaks out.
hw(bm.A, True); advance(50); hw(bm.B, True); acts = btn.poll()
check("T6 B pressed while A down does not cycle", acts == [], str(acts))
advance(3100); acts = btn.poll()
check("T6 A+B 3 s = OTA unlock", acts == [bm.AB_HOLD], str(acts))
advance(500); hw(bm.A, False); hw(bm.B, False); acts = btn.poll()
check("T6 combo release leaks nothing", acts == [], str(acts))

# T7: A short toggles temp; A hold (2 s) = LP mode.
hw(bm.A, True); advance(100); hw(bm.A, False); acts = btn.poll()
check("T7 A short", acts == [bm.A_SHORT], str(acts))
hw(bm.A, True); advance(2050); acts = btn.poll()
check("T7 A hold", acts == [bm.A_HOLD], str(acts))
hw(bm.A, False); check("T7 A hold release nothing", btn.poll() == [])

# T8: ticks wraparound (supervisor.ticks_ms wraps at 2**29).
CLOCK["ms"] = (1 << 29) - 100
hw(bm.C, True); CLOCK["ms"] = 60; hw(bm.C, False); acts = btn.poll()
check("T8 short press across ticks wrap", acts == [bm.C_SHORT], str(acts))
CLOCK["ms"] = (1 << 29) - 100
hw(bm.C, True); CLOCK["ms"] = 2100; acts = btn.poll()
check("T8 hold across ticks wrap", acts == [bm.C_HOLD], str(acts))
hw(bm.C, False); btn.poll()

# T9: code.py wiring contract.
code_src = open("../code.py").read()
check("T9 code.py builds Buttons", "buttons_mod.Buttons(" in code_src)
check("T9 code.py dispatches via poll()", "for action in buttons.poll():" in code_src)
check("T9 no digitalio edge detection left", "read_b()" not in code_src and "prev_b" not in code_src)
for w in ('_busy("cloud"', '_busy("mqtt"', '_busy("ntp"', '_busy("wifi"'):
    check("T9 busy-wrapped " + w, w in code_src)
check("T9 poll hook registered", "poll_buttons=lambda: buttons.scan()" in code_src)

print()
print("ALL PASS" if fails == 0 else "%d FAILURE(S)" % fails)
sys.exit(1 if fails else 0)
