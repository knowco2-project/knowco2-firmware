# knowco2/ui/../sensors/sunrise.py
# ----------------------------------------------------------------------
# Driver for the Senseair Sunrise / Sunlight NDIR CO2 sensor.
#
# This driver talks to the sensor over RAW I2C (no vendor CircuitPython
# library required) — a useful demonstration that a CO2Sensor driver only
# needs to satisfy the contract in base.py; it does NOT need an Adafruit
# library the way the SCD-4x / SCD-30 drivers do.
#
# Protocol reference: Senseair "I2C on Sunrise & Sunlight" (TDE5531).
#   * 7-bit I2C address: 0x68
#   * Reads use a repeated-START (write register pointer, then read), which
#     CircuitPython exposes as i2c.writeto_then_readfrom(...).
#   * Read-only measurement block (continuous-measurement mode):
#       0x06  int16  CO2, filtered & pressure-compensated, ppm   <- primary
#       0x08  int16  chip temperature, deg C * 100
#       0x00  uint16 error status (0 = OK)
#   * Sensor ID lives at 0x3A..0x3D (4 bytes) — surfaced as the serial.
#
# IMPORTANT — verify on hardware: the CO2/temperature reads and the 0x68
# address are well documented and used here directly. The calibration /
# soft-reset register writes follow TDE5531 but are best-effort and wrapped
# so a wrong value can never crash the firmware — confirm the command bytes
# against the datasheet revision for your specific unit before relying on
# field calibration. The Sunrise has NO humidity channel, so read() returns
# rh = None (the UI shows "--%" for humidity in that case).
# ----------------------------------------------------------------------

import time

from .base import CO2Sensor

_ADDR = 0x68

# Read-only measurement registers
_REG_ERROR_STATUS = 0x00
_REG_CO2_FILTERED = 0x06   # int16, ppm
_REG_TEMPERATURE  = 0x08   # int16, degC * 100
_REG_SENSOR_ID    = 0x3A   # 4 bytes

# Write registers (best-effort; see header note)
_REG_CALIB_STATUS  = 0x81  # write 0x00 to clear before a calibration
_REG_CALIB_COMMAND = 0x82  # int16 command word
_REG_CALIB_TARGET  = 0x84  # int16 target ppm (for target calibration)
_REG_METER_CONTROL = 0xA5  # bit0 nRDY, etc.; ABC enable bit
_REG_SOFT_RESET    = 0xA3  # write 0xFF to soft-reset

# Calibration command words (TDE5531)
_CMD_TARGET_CALIB = 0x7C03  # forced calibration to CalibrationTarget
_CMD_FACTORY_RST  = 0x7C02  # restore factory calibration


def _s16(hi, lo):
    v = (hi << 8) | lo
    return v - 0x10000 if v & 0x8000 else v


class SunriseSensor(CO2Sensor):
    model = "Sunrise"
    supports_low_power = False   # continuous mode here; single-shot LP is a
    normal_period_s = 5.0        # documented extension (see header).

    def __init__(self, i2c):
        super().__init__(i2c)
        self._i2c = i2c
        self._last_read = 0.0

    # ------------------------------------------------------------------
    # Low-level raw-I2C helpers
    # ------------------------------------------------------------------
    def _read_reg(self, reg, nbytes):
        i2c = self._i2c
        buf = bytearray(nbytes)
        while not i2c.try_lock():
            pass
        try:
            # Repeated-START: write the register pointer, then read N bytes.
            i2c.writeto_then_readfrom(_ADDR, bytes([reg]), buf)
        finally:
            i2c.unlock()
        return buf

    def _write_reg(self, reg, payload):
        i2c = self._i2c
        while not i2c.try_lock():
            pass
        try:
            i2c.writeto(_ADDR, bytes([reg]) + bytes(payload))
        finally:
            i2c.unlock()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    @classmethod
    def detect(cls, i2c):
        try:
            present = False
            while not i2c.try_lock():
                pass
            try:
                present = _ADDR in i2c.scan()
            finally:
                i2c.unlock()
            if not present:
                return None
            inst = cls(i2c)
            inst.serial = inst._read_serial_raw()
            # Sanity read — make sure we actually get a value back.
            inst.read()
            print("Sunrise detected at 0x%02X" % _ADDR)
            return inst
        except Exception as e:
            print("Sunrise detect failed:", e)
            return None

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    @property
    def data_ready(self):
        # Continuous mode refreshes every measurement period; gate softly so
        # we don't hammer the bus between updates. (The main loop already
        # polls at ~1 Hz.)
        return (time.monotonic() - self._last_read) >= 2.0

    def read(self):
        # Read CO2 and temperature. Raises on I2C error -> the caller treats a
        # raised exception as a failed sample and will trigger recovery.
        co2_buf = self._read_reg(_REG_CO2_FILTERED, 2)
        co2 = _s16(co2_buf[0], co2_buf[1])
        temp_c = None
        try:
            t_buf = self._read_reg(_REG_TEMPERATURE, 2)
            temp_c = _s16(t_buf[0], t_buf[1]) / 100.0
        except Exception:
            temp_c = None
        self._last_read = time.monotonic()
        # No humidity channel on the Sunrise.
        return (float(co2), temp_c, None)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    def _read_serial_raw(self):
        try:
            b = self._read_reg(_REG_SENSOR_ID, 4)
            return "%02X%02X%02X%02X" % (b[0], b[1], b[2], b[3])
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Calibration & compensation (best-effort; verify register map)
    # ------------------------------------------------------------------
    def set_asc(self, enabled):
        # ABC (Automatic Baseline Correction) is controlled via MeterControl.
        # Best-effort: read-modify-write would need the current byte; we keep
        # it simple and skip if uncertain to avoid writing a wrong value.
        pass

    def set_altitude(self, meters):
        # Sunrise compensates by barometric pressure, not altitude — no-op.
        pass

    def set_ambient_pressure(self, hpa):
        # Pressure compensation exists on some revisions; left best-effort/no-op
        # here. See TDE5531 for the air-pressure register on your unit.
        pass

    def force_calibration(self, ref_ppm):
        # Target calibration: write the target ppm, clear status, send command.
        try:
            ref = int(ref_ppm)
            self._write_reg(_REG_CALIB_TARGET, [(ref >> 8) & 0xFF, ref & 0xFF])
            self._write_reg(_REG_CALIB_STATUS, [0x00])
            self._write_reg(_REG_CALIB_COMMAND,
                            [(_CMD_TARGET_CALIB >> 8) & 0xFF, _CMD_TARGET_CALIB & 0xFF])
            time.sleep(4)   # calibration takes one measurement cycle
            return True
        except Exception as e:
            print("Sunrise force_calibration error:", e)
            return False

    # ------------------------------------------------------------------
    # Power / lifecycle primitives (used by base recover()/set_low_power())
    # ------------------------------------------------------------------
    def _start_normal_raw(self):
        # Continuous mode resumes on its own after reset; nothing to do.
        return True

    def _stop_raw(self):
        pass

    def _soft_reset_raw(self):
        # Soft reset: write 0xFF to the reset register (best-effort).
        self._safe(self._write_reg, _REG_SOFT_RESET, [0xFF])
