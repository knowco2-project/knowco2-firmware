"""Experimental, RAM-only energy accounting. No electrical measurements.

Integer integration is exact for the supplied piecewise-constant model. Model
accuracy must be established with a reference instrument. No files, bus reads,
network calls, sleeps, or sample history live in this module.
"""
from array import array

TICKS_PERIOD = 1 << 29
TICKS_MASK = TICKS_PERIOD - 1
TICKS_HALF = TICKS_PERIOD // 2
LIMIT = (1 << 30) - 1
MAX_POWER_MW = 5000
MAX_GAP_MS = 120000
WINDOW_MS = 60000
UJ_PER_MWH = 3600000
GAP, CLOCK_INVALID, SATURATED = 1, 2, 4
# Fixed 36-byte numeric buffer; Python code/objects require additional heap.
E, REM, COVERED_S, COVERED_MS, UNKNOWN_S, UNKNOWN_MS, FLAGS, WIN_UJ, WIN_MS = range(9)


def ticks_diff(new, old):
    return ((new - old + TICKS_HALF) & TICKS_MASK) - TICKS_HALF


class Accumulator:
    def __init__(self):
        self.values = array("I", (0, 0, 0, 0, 0, 0, 0, 0, 0))
        if self.values.itemsize != 4:
            raise RuntimeError("energy requires 32-bit array elements")
        self.last_tick = None
        self.power_mw = None
        self.average_mw = None

    def _time(self, index, elapsed):
        s = self.values
        fraction = s[index + 1] + elapsed % 1000
        seconds = elapsed // 1000 + fraction // 1000
        if seconds > LIMIT - s[index]:
            s[FLAGS] |= SATURATED
            return
        s[index] += seconds
        s[index + 1] = fraction % 1000

    def step(self, now, next_power, discard=False):
        """Account old power until now; then change to next_power.

        discard excludes an interval whose actual transition time is unknown.
        None is unknown power, not 0 W. A clock fault/saturation latches until
        reboot, so downstream statistics cannot silently continue as valid.
        """
        if type(now) is not int or not 0 <= now < TICKS_PERIOD:
            raise ValueError("invalid tick")
        if next_power is not None and (
                type(next_power) is not int or not 0 <= next_power <= MAX_POWER_MW):
            raise ValueError("power must be bounded integer mW or None")
        elapsed = 0 if self.last_tick is None else ticks_diff(now, self.last_tick)
        old_power = self.power_mw
        self.last_tick = now
        self.power_mw = next_power
        s = self.values
        if elapsed < 0:
            s[FLAGS] |= CLOCK_INVALID
        if s[FLAGS] & (CLOCK_INVALID | SATURATED):
            self.power_mw = self.average_mw = None
            return
        if elapsed:
            if discard or old_power is None or elapsed > MAX_GAP_MS:
                self._time(UNKNOWN_S, elapsed)
                s[WIN_UJ] = s[WIN_MS] = 0
                self.average_mw = None
                if elapsed > MAX_GAP_MS:
                    s[FLAGS] |= GAP
            else:
                self._time(COVERED_S, elapsed)
                # At most 603,599,999: stays inside a signed 30-bit small int.
                subtotal = s[REM] + old_power * elapsed
                added = subtotal // UJ_PER_MWH
                if added > LIMIT - s[E]:
                    s[FLAGS] |= SATURATED
                else:
                    s[E] += added
                    s[REM] = subtotal % UJ_PER_MWH
                # A window can overrun by one bounded step, still < 2**30.
                s[WIN_UJ] += old_power * elapsed
                s[WIN_MS] += elapsed
                if s[WIN_MS] >= WINDOW_MS:
                    self.average_mw = s[WIN_UJ] // s[WIN_MS]
                    s[WIN_UJ] = s[WIN_MS] = 0
        if next_power is None or s[FLAGS] & (CLOCK_INVALID | SATURATED):
            self.average_mw = None
            s[WIN_UJ] = s[WIN_MS] = 0

    def snapshot(self):
        """Allocate only for telemetry/diagnostics, never on periodic steps."""
        s = self.values
        valid = not bool(s[FLAGS] & (CLOCK_INVALID | SATURATED))
        return {
            "energy_mwh_est": s[E] if valid else None,
            "residual_uj": s[REM] if valid else None,
            "power_mw_est": self.average_mw if valid else None,
            "covered_s": s[COVERED_S],
            "covered_ms_remainder": s[COVERED_MS],
            "unmodeled_s": s[UNKNOWN_S],
            "unmodeled_ms_remainder": s[UNKNOWN_MS],
            "flags": s[FLAGS],
        }
