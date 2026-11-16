"""Opt-in engineering observer; deliberately independent of CO2 acquisition.

Only cached firmware state and the display brightness property are observed.
Source/battery absence are OPERATOR declarations, not hardware detection.
"""
from .energy import Accumulator, ticks_diff, COVERED_S, UNKNOWN_S, FLAGS

BUILD_ID = "energy-experiment-v1"


class Monitor:
    def __init__(self, state, board, version, profile, ticks_ms, clock, radio=None):
        self.state = state
        self.board = board
        self.version = version
        self.ticks_ms = ticks_ms
        self.clock = clock
        self.radio = radio
        self.counter = Accumulator()
        self.activity = "idle"
        self.context = None
        self.last_http = state.last_http_ts
        self.last_reset = None
        self.profile = profile
        self.reason = "unconfigured"
        self._validate()
        self.poll(force=True)

    def _validate(self):
        p = self.profile
        if p is None:
            return
        if not isinstance(p, dict):
            raise ValueError("invalid power profile")
        for field in ("id", "reference", "adapter"):
            if not isinstance(p.get(field), str) or not 0 < len(p[field]) <= 96:
                raise ValueError("missing/bounded profile metadata")
        if p.get("boundary") not in ("wall_ac", "usb_dc"):
            raise ValueError("only externally powered, no-battery experiments supported")
        if p.get("battery_absent_confirmed") is not True:
            raise ValueError("battery absence must be confirmed by the operator")
        if p.get("build_id") != BUILD_ID:
            raise ValueError("profile build mismatch")
        for key, actual in (
            ("board_id", self.state.board_id_str),
            ("sensor_model", self.state.sensor_model_str),
            ("firmware", self.version.FIRMWARE_VERSION),
            ("circuitpython", self.version.CP_VERSION),
        ):
            if p.get(key) != actual:
                raise ValueError("profile mismatch: " + key)
        modes = p.get("modes")
        if not isinstance(modes, dict) or not 1 <= len(modes) <= 32:
            raise ValueError("profile must contain 1..32 mode rows")
        for key, power in modes.items():
            if not isinstance(key, tuple) or len(key) != 8:
                raise ValueError("invalid mode key")
            if type(power) is not int or not 0 <= power <= 5000:
                raise ValueError("invalid mode power")
        self.reason = "experimental"

    def _context(self):
        s = self.state
        return (bool(s.energy_mode), int(round(s._scd_period_effective * 1000)),
                int(round(self.board.DISPLAY.brightness * 1000)),
                s.wifi_mode, bool(self.radio.connected) if self.radio is not None else False,
                s.display_mode, s.screen)

    def _power(self, context, activity):
        s = self.state
        if self.profile is None or s.sensor is None or s.scd_init_failed:
            return None
        if s.sensor_model_str != self.profile["sensor_model"] or s.sensor_frozen_shown:
            return None
        return self.profile["modes"].get(context + (activity,))

    def poll(self, force=False, activity=None):
        now = self.ticks_ms()
        if not force and self.counter.last_tick is not None:
            delta = ticks_diff(now, self.counter.last_tick)
            if 0 <= delta < 1000:
                return
        context = self._context()
        http = self.state.last_http_ts
        # An unbracketed settings/screen/HTTP change has no reliable timestamp.
        # Exclude that interval rather than retroactively applying new power.
        discard = (self.context is not None and context != self.context) or http != self.last_http
        s = self.state
        discard = discard or s.sensor is None or s.scd_init_failed or s.sensor_frozen_shown
        if activity is not None:
            self.activity = activity
        power = self._power(context, self.activity)
        self.counter.step(now, power, discard=discard)
        self.context = context
        self.last_http = http
        if self.last_reset is None and self.state.ntp_synced:
            elapsed_s = self.counter.values[COVERED_S] + self.counter.values[UNKNOWN_S]
            stamp = self.clock.time() - elapsed_s
            parts = self.clock.localtime(stamp)
            if 2020 <= parts[0] <= 2099:
                self.last_reset = "%04d-%02d-%02dT%02d:%02d:%02dZ" % tuple(parts[:6])

    def transition(self, activity):
        self.poll(force=True, activity=activity)

    def snapshot(self):
        # No polling, gauge I/O, integration, or flash writes from a consumer.
        data = self.counter.snapshot()
        p = self.profile
        data.update({
            "method": "experimental_model",
            "profile_id": p["id"] if p else None,
            "boundary": p["boundary"] if p else "unknown",
            "source_basis": "operator_declared_no_battery" if p else "unknown",
            "last_reset": self.last_reset,
            "mode": self.context + (self.activity,),
            "configured": p is not None,
            "quality": self.reason,
        })
        if p is None:
            data["energy_mwh_est"] = data["residual_uj"] = None
        return data
