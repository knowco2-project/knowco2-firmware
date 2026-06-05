# knowco2/sensors/stcc4.py
# ----------------------------------------------------------------------
# Driver for the Sensirion STCC4 (+ onboard SHT41) — Adafruit's
# adafruit_stcc4 CircuitPython library.
#
# The STCC4 is a thermal-conductivity CO2 sensor with a paired SHT41 for
# temperature + humidity (so it reports all three, unlike the Sunrise).
# Quirks this driver hides from the rest of the firmware:
#   * reading `.CO2` is what triggers a fresh measurement and refreshes
#     temperature/humidity — so read() reads CO2 FIRST, then temp/rh;
#   * there's no `data_ready` flag, so we soft-gate sampling to the
#     measurement period (5 s normal, 30 s in low power);
#   * low power = stop continuous + single-shot on demand (the library
#     auto-triggers a single shot when continuous is off);
#   * no host ASC toggle and no altitude — it compensates by pressure;
#   * forced recalibration returns 0xFFFF on failure.
#
# The adafruit_stcc4 import is optional: if the library isn't on the board,
# detect() simply returns None and the registry moves on. So you can drop
# this file in now and add `lib/adafruit_stcc4.mpy` whenever you get the
# hardware — nothing else needs to change.
# ----------------------------------------------------------------------

import time

try:
    import adafruit_stcc4
except Exception:
    adafruit_stcc4 = None

from .base import CO2Sensor

_FRC_FAILED = 0xFFFF   # forced_recalibration() sentinel for failure


class STCC4Sensor(CO2Sensor):
    model = "STCC4"
    supports_low_power = True
    normal_period_s = 5.0
    low_power_period_s = 30.0

    def __init__(self, device):
        super().__init__(device)
        self._last_read = 0.0
        self._gate = self.normal_period_s

    @classmethod
    def detect(cls, i2c):
        if adafruit_stcc4 is None:
            return None
        try:
            # Constructor soft-resets and verifies the product ID, raising if
            # the device at 0x64 isn't an STCC4 — so a failure here is a clean
            # "not present", and the registry tries the next driver.
            dev = adafruit_stcc4.STCC4(i2c)
        except Exception as e:
            print("STCC4 not found:", e)
            return None
        try:
            inst = cls(dev)
            inst.model = "STCC4"
            inst.serial = inst._read_serial_raw()
            dev.continuous_measurement = True      # start 1 s continuous mode
            inst._gate = cls.normal_period_s
            inst._last_read = 0.0
            return inst
        except Exception as e:
            print("STCC4 init failed:", e)
            return None

    # --- data ---
    @property
    def data_ready(self):
        # No hardware data-ready line; pace sampling to the measurement period.
        return (time.monotonic() - self._last_read) >= self._gate

    def read(self):
        # IMPORTANT: read CO2 first — that's what refreshes the cached
        # temperature/humidity inside the library. May raise on a CRC/I2C
        # error, which the caller treats as a failed sample.
        co2 = self.device.CO2
        temp_c = self.device.temperature
        rh = self.device.relative_humidity
        self._last_read = time.monotonic()
        return (float(co2), temp_c, rh)

    # --- identity ---
    def _read_serial_raw(self):
        # The STCC4 exposes a 32-bit product ID rather than a unit serial.
        try:
            return "PID-%08X" % self.device.product_id
        except Exception:
            return None

    # --- calibration / compensation ---
    def set_asc(self, enabled):
        # No host-controlled ASC toggle on the STCC4 in this library — no-op.
        pass

    def set_altitude(self, meters):
        # STCC4 compensates by barometric pressure, not altitude — no-op.
        pass

    def set_ambient_pressure(self, hpa):
        if hpa:
            self._safe(self.device.pressure_compensation, int(hpa))

    def force_calibration(self, ref_ppm):
        try:
            result = self.device.forced_recalibration(int(ref_ppm))
            return result != _FRC_FAILED
        except Exception as e:
            print("STCC4 force_calibration error:", e)
            return False

    # --- power / lifecycle primitives (used by base set_low_power/recover) ---
    def _start_normal_raw(self):
        self.device.continuous_measurement = True
        self._gate = self.normal_period_s
        self._last_read = 0.0
        return True

    def _start_low_power_raw(self):
        # Low power: leave continuous off. Reading .CO2 then auto-triggers a
        # single-shot measurement, which we pace to low_power_period_s.
        self._safe(setattr, self.device, "continuous_measurement", False)
        self._gate = self.low_power_period_s
        self._last_read = 0.0
        return True

    def _stop_raw(self):
        self._safe(setattr, self.device, "continuous_measurement", False)

    def _soft_reset_raw(self):
        self._safe(self.device.reset)
