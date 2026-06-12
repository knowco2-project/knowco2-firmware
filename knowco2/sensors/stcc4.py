# knowco2/sensors/stcc4.py
# ----------------------------------------------------------------------
# Driver for the Sensirion STCC4 (+ onboard SHT41) — Adafruit's
# adafruit_stcc4 CircuitPython library.
#
# The STCC4 is a thermal-conductivity CO2 sensor with a paired SHT41 for
# temperature + humidity (so it reports all three, unlike the Sunrise).
#
# MODE MODEL (verified against the library source + datasheet):
#   * The STCC4 has NO low-power *periodic* mode like the SCD41. Its modes
#     are: continuous (1 Hz), idle, single-shot-from-idle, and a deep
#     sleep_mode (unused here — wake sequencing isn't worth the complexity
#     at a 30 s cadence; idle current is already low).
#   * "Low power" in this driver = Sensirion's recommended single-shot
#     scheme: stop continuous, then trigger one measurement per LP period.
#     Reading `.CO2` while continuous is off auto-triggers a single shot
#     (which blocks ~0.5 s inside the library).
#
# CRITICAL QUIRK this driver must defend against:
#   The library's `.CO2` property decides whether to fire a single shot
#   based on its INTERNAL `_continuous` flag — not hardware state. In the
#   library's setter, the flag is only updated AFTER the I2C write
#   succeeds, and a soft reset() returns the HARDWARE to idle without
#   touching the flag. Any NAK or reset can therefore desync flag vs.
#   hardware, after which `.CO2` silently stops triggering measurements
#   and the sensor appears frozen. _set_continuous() below force-syncs
#   the flag on every mode change, and _soft_reset_raw() re-syncs it
#   after a reset. Do not bypass these helpers.
#
# Other quirks hidden from the rest of the firmware:
#   * reading `.CO2` is what refreshes temperature/humidity — read CO2 first;
#   * there's no data-ready line, so sampling is paced to the period;
#   * after a stop/mode change we wait a full period before the first
#     read, so the sensor has settled (an immediate single shot after
#     STOP can NAK and start the failure counter for no reason);
#   * no host ASC toggle and no altitude — it compensates by pressure;
#   * forced recalibration returns 0xFFFF on failure;
#   * perform_conditioning() exists (improves initial single-shot
#     accuracy) but BLOCKS ~22 s — longer than the 20 s hardware
#     watchdog — so it is deliberately NOT called at runtime. Run it
#     once during pre-ship calibration, before the outdoor FRC.
#
# The adafruit_stcc4 import is optional: if the library isn't on the board,
# detect() simply returns None and the registry moves on.
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
    supports_low_power = True      # single-shot emulation (see header)
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
            inst._set_continuous(True)         # start 1 s continuous mode
            inst._gate = cls.normal_period_s
            # Library setter sleeps 1 s after START, so data exists already;
            # still wait one period before the first read for a clean sample.
            inst._last_read = time.monotonic()
            return inst
        except Exception as e:
            print("STCC4 init failed:", e)
            return None

    # ------------------------------------------------------------------
    # Continuous-mode helper — ALWAYS use this, never set the library
    # property directly.  It (a) attempts the hardware write, swallowing
    # a NAK (e.g. STOP while already idle is invalid and NAKs), and
    # (b) force-syncs the library's internal `_continuous` flag to the
    # intended state, because the library only updates that flag when the
    # write succeeds.  Without (b), a single NAK leaves `.CO2` believing
    # the wrong mode and it stops triggering measurements entirely.
    # ------------------------------------------------------------------
    def _set_continuous(self, value):
        value = bool(value)
        self._safe(setattr, self.device, "continuous_measurement", value)
        try:
            self.device._continuous = value
        except Exception:
            pass

    # --- data ---
    @property
    def data_ready(self):
        # No hardware data-ready line; pace sampling to the measurement period.
        return (time.monotonic() - self._last_read) >= self._gate

    def read(self):
        # IMPORTANT: read CO2 first — that's what refreshes the cached
        # temperature/humidity inside the library, and (in LP) triggers the
        # single-shot measurement (~0.5 s blocking). May raise on a CRC/I2C
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
        self._set_continuous(True)
        self._gate = self.normal_period_s
        # Wait one period before the first read (library setter already
        # slept 1 s for the first sample; this just paces us cleanly).
        self._last_read = time.monotonic()
        return True

    def _start_low_power_raw(self):
        # Low power: leave continuous off. Reading .CO2 then auto-triggers a
        # single-shot measurement, which we pace to low_power_period_s.
        self._set_continuous(False)
        self._gate = self.low_power_period_s
        # CRITICAL: wait a full LP period before the first single shot.
        # The STOP command needs time to execute; an immediate single shot
        # can NAK and feed the firmware's failure counter for no reason.
        self._last_read = time.monotonic()
        return True

    def _stop_raw(self):
        self._set_continuous(False)

    def _soft_reset_raw(self):
        self._safe(self.device.reset)
        # A soft reset returns the HARDWARE to idle but the library flag is
        # untouched — re-sync it so `.CO2` behaves correctly until the
        # recover() sequence restarts the intended mode.
        try:
            self.device._continuous = False
        except Exception:
            pass
