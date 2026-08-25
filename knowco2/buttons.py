# knowco2/buttons.py
# ----------------------------------------------------------------------
# Button input that never drops a press (RC-52).
#
# WHY THIS EXISTS
# ---------------
# RC-51 and earlier detected presses by comparing the pin level to the
# level seen on the *previous main-loop pass* (edge detection). That only
# works if the main loop samples the pin while the button is down. The
# main loop is single-threaded and regularly blocks for 1–8 s inside
# network calls (TLS POST to the cloud, MQTT, NTP, Wi-Fi reconnect, serving
# a web page). A press + release that lands entirely inside one of those
# windows leaves the pin low on both sides of the block, so the edge
# detector never sees it — the press is silently lost. With cloud uploads
# every 60 s that window is open a large fraction of the time, which is
# exactly the "I press B and nothing happens" symptom.
#
# FIX
# ---
# Use CircuitPython's `keypad` module. It scans the pins from the
# supervisor tick (a hardware timer, independent of what Python is doing)
# and pushes timestamped press/release events into a queue. The main loop
# drains the queue whenever it gets control, so a press made during a
# blocking call is applied the instant the call returns — and, because
# events carry timestamps, a 2 s hold that happened while we were blocked
# is still classified as a hold, not a short press.
#
# When `keypad` is unavailable (browser simulator / host tests) we fall
# back to digitalio sampling with the same event interface, so the rest of
# the firmware sees identical behaviour in both environments.
#
# Button map (Feather ESP32-S3 Reverse TFT):
#   A = D0 (active-low, pull-up)   B = D1 (active-high, pull-down)
#   C = D2 (active-high, pull-down)
# ----------------------------------------------------------------------

import time

try:
    import keypad as _keypad
    import supervisor as _supervisor
except ImportError:
    _keypad = None
    _supervisor = None

A = 0
B = 1
C = 2
NAMES = ("A", "B", "C")

# Action names yielded by Buttons.poll()
A_SHORT = "a_short"
A_HOLD = "a_hold"
B_SHORT = "b_short"
B_HOLD = "b_hold"
C_SHORT = "c_short"
C_HOLD = "c_hold"
AB_HOLD = "ab_hold"

_TICKS_PERIOD = 1 << 29
_TICKS_HALF = 1 << 28


def _ticks_diff(a, b):
    """a - b in ms, wrap-safe for supervisor.ticks_ms()."""
    return ((a - b + _TICKS_HALF) & (_TICKS_PERIOD - 1)) - _TICKS_HALF


# ----------------------------------------------------------------------
# Event sources
# ----------------------------------------------------------------------
class _KeypadSource:
    """Hardware-timer scanned, queued events. Two Keys objects because A is
    active-low and B/C are active-high (value_when_pressed is per-object)."""

    def __init__(self, pin_a, pin_b, pin_c, interval=0.015):
        self._ka = _keypad.Keys((pin_a,), value_when_pressed=False, pull=True,
                                interval=interval, max_events=16)
        self._kbc = _keypad.Keys((pin_b, pin_c), value_when_pressed=True, pull=True,
                                 interval=interval, max_events=32)
        self._ev = _keypad.Event()

    def now_ms(self):
        return _supervisor.ticks_ms()

    def events(self):
        """Yield (key, pressed, ts_ms) in arrival order."""
        out = []
        ev = self._ev
        # Drain both queues, then merge by timestamp so A+B ordering is right.
        while self._ka.events.get_into(ev):
            out.append((A, ev.pressed, ev.timestamp))
        while self._kbc.events.get_into(ev):
            out.append((B if ev.key_number == 0 else C, ev.pressed, ev.timestamp))
        if len(out) > 1:
            base = out[0][2]
            out.sort(key=lambda e: _ticks_diff(e[2], base))
        return out

    def overflowed(self):
        o = self._ka.events.overflowed or self._kbc.events.overflowed
        if o:
            self._ka.events.clear()
            self._kbc.events.clear()
        return o

    def deinit(self):
        try:
            self._ka.deinit()
            self._kbc.deinit()
        except Exception:
            pass


class _PollSource:
    """digitalio fallback (simulator / hosts without keypad). Same interface;
    scan() must be called frequently — blocking code calls runtime.poll_buttons()."""

    def __init__(self, pin_a, pin_b, pin_c):
        import digitalio
        self._a = digitalio.DigitalInOut(pin_a)
        self._a.switch_to_input(pull=digitalio.Pull.UP)
        self._b = digitalio.DigitalInOut(pin_b)
        self._b.switch_to_input(pull=digitalio.Pull.DOWN)
        self._c = digitalio.DigitalInOut(pin_c)
        self._c.switch_to_input(pull=digitalio.Pull.DOWN)
        self._prev = [False, False, False]
        self._queue = []

    def now_ms(self):
        return int(time.monotonic() * 1000) & (_TICKS_PERIOD - 1)

    def _read(self):
        return (not self._a.value, bool(self._b.value), bool(self._c.value))

    def sample(self):
        """Latch edges into the queue. Cheap; safe to call from blocking code."""
        try:
            cur = self._read()
        except Exception:
            return
        ts = self.now_ms()
        for k in (A, B, C):
            if cur[k] != self._prev[k]:
                self._prev[k] = cur[k]
                self._queue.append((k, cur[k], ts))

    def events(self):
        self.sample()
        out = self._queue
        self._queue = []
        return out

    def overflowed(self):
        return False

    def deinit(self):
        for p in (self._a, self._b, self._c):
            try:
                p.deinit()
            except Exception:
                pass


# ----------------------------------------------------------------------
# Gesture engine
# ----------------------------------------------------------------------
class Buttons:
    """Turns raw press/release events into actions.

    Gestures (identical to RC-51 semantics):
      A short / A hold (LP_A_HOLD)             B short / B hold (B_HOLD, guarded)
      C short / C hold (D2_HOLD)               A+B hold (OTA unlock)

    Differences from RC-51 (all in the user's favour):
      * Presses made while the main loop is blocked are queued, not lost.
      * Holds are measured from event timestamps, so a hold completed while
        blocked is still a hold.
      * B fires on *press* (not release) when `b_fire_on_press()` says it is
        safe — on the main screen B has no hold action, so there is nothing
        to wait for. That makes view cycling feel instant.
    """

    def __init__(self, pin_a, pin_b, pin_c,
                 a_hold_s=2.0, b_hold_s=2.0, c_hold_s=2.0, ab_hold_s=3.0,
                 b_hold_allowed=None, b_fire_on_press=None, force_poll=False):
        if _keypad is not None and _supervisor is not None and not force_poll:
            self._src = _KeypadSource(pin_a, pin_b, pin_c)
            self.backend = "keypad"
        else:
            self._src = _PollSource(pin_a, pin_b, pin_c)
            self.backend = "poll"
        self._hold_ms = {A: int(a_hold_s * 1000), B: int(b_hold_s * 1000),
                         C: int(c_hold_s * 1000)}
        self._ab_hold_ms = int(ab_hold_s * 1000)
        self._b_hold_allowed = b_hold_allowed or (lambda: True)
        self._b_fire_on_press = b_fire_on_press or (lambda: False)

        self.down = [False, False, False]
        self._press_ts = [None, None, None]
        self._consumed = [False, False, False]   # hold fired / press-fired / suppressed
        self._ab_start = None
        self._ab_fired = False
        self._actions = []
        self.dropped = 0          # queue overflow counter (diagnostic)
        self.last_event_ms = None

    # -- public ---------------------------------------------------------
    def scan(self):
        """Drain events + evaluate holds. Safe to call from anywhere, including
        inside blocking operations (runtime.poll_buttons). Never raises."""
        try:
            if self._src.overflowed():
                self.dropped += 1
            for key, pressed, ts in self._src.events():
                self.last_event_ms = ts
                if pressed:
                    self._on_press(key, ts)
                else:
                    self._on_release(key, ts)
            self._check_holds(self._src.now_ms())
        except Exception:
            pass

    def poll(self):
        """scan() and return the list of actions that became due, in order."""
        self.scan()
        out = self._actions
        self._actions = []
        return out

    def is_down(self, key):
        return self.down[key]

    def deinit(self):
        self._src.deinit()

    # -- internals ------------------------------------------------------
    def _hold_ok(self, key):
        if key == B:
            try:
                return bool(self._b_hold_allowed())
            except Exception:
                return False
        return True

    def _on_press(self, key, ts):
        self.down[key] = True
        self._press_ts[key] = ts
        self._consumed[key] = False

        if key in (A, B) and self.down[A] and self.down[B]:
            # Combo engaged: both single-button gestures are suppressed.
            self._ab_start = ts
            self._ab_fired = False
            self._consumed[A] = True
            self._consumed[B] = True
            return

        if key == B:
            try:
                fire_now = bool(self._b_fire_on_press())
            except Exception:
                fire_now = False
            if fire_now:
                self._consumed[B] = True
                self._actions.append(B_SHORT)

    def _on_release(self, key, ts):
        self.down[key] = False
        start = self._press_ts[key]
        self._press_ts[key] = None
        if key in (A, B):
            self._ab_start = None
            self._ab_fired = False
        if start is None or self._consumed[key]:
            self._consumed[key] = False
            return
        self._consumed[key] = False
        # A complete gesture was queued while we were blocked: classify it by
        # its real duration, not by when we got around to looking.
        held = _ticks_diff(ts, start)
        if held >= self._hold_ms[key] and self._hold_ok(key):
            self._actions.append((A_HOLD, B_HOLD, C_HOLD)[key])
        else:
            self._actions.append((A_SHORT, B_SHORT, C_SHORT)[key])

    def _check_holds(self, now):
        # A+B combo
        if self.down[A] and self.down[B] and self._ab_start is not None:
            if (not self._ab_fired) and _ticks_diff(now, self._ab_start) >= self._ab_hold_ms:
                self._ab_fired = True
                self._actions.append(AB_HOLD)
            return
        # Single-button holds
        for key in (A, B, C):
            if not self.down[key] or self._consumed[key]:
                continue
            start = self._press_ts[key]
            if start is None:
                continue
            if _ticks_diff(now, start) >= self._hold_ms[key] and self._hold_ok(key):
                self._consumed[key] = True
                self._actions.append((A_HOLD, B_HOLD, C_HOLD)[key])
