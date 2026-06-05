# knowco2/sensors/scd30.py
# ----------------------------------------------------------------------
# Driver for Sensirion SCD30 (Adafruit adafruit_scd30 library).
#
# The SCD30 differs from the SCD-4x in several ways that the rest of the
# firmware no longer has to know about:
#   * it starts continuous measurement automatically on construction;
#   * "data ready" is `data_available`, not `data_ready`;
#   * it has no serial_number (we surface firmware_version instead);
#   * ambient pressure is a settable property, not a method;
#   * forced recalibration uses `forced_recalibration_reference`;
#   * it has no low-power mode.
# ----------------------------------------------------------------------

import time

try:
    import adafruit_scd30
except Exception:
    adafruit_scd30 = None

from .base import CO2Sensor


class SCD30Sensor(CO2Sensor):
    model = "SCD30"
    supports_low_power = False
    normal_period_s = 5.0

    @classmethod
    def detect(cls, i2c):
        if adafruit_scd30 is None:
            return None
        try:
            dev = adafruit_scd30.SCD30(i2c)
        except Exception as e:
            print("SCD30 construct failed:", e)
            return None
        try:
            inst = cls(dev)
            inst.model = "SCD30"
            # SCD30 starts continuous measurement automatically; no start call.
            inst.serial = inst._read_serial_raw()
            return inst
        except Exception as e:
            print("SCD30 init failed:", e)
            return None

    # --- data ---
    @property
    def data_ready(self):
        return self.device.data_available

    def read(self):
        return (self.device.CO2, self.device.temperature, self.device.relative_humidity)

    # --- identity ---
    def _read_serial_raw(self):
        # No serial_number on the SCD30; label it with firmware_version.
        fw = getattr(self.device, "firmware_version", None)
        if fw is None:
            return None
        try:
            if isinstance(fw, (tuple, list)) and len(fw) >= 2:
                return "FW%d.%d" % (fw[0], fw[1])
            return "FW" + str(fw)
        except Exception:
            return None

    # --- calibration / compensation ---
    def set_asc(self, enabled):
        self._safe(setattr, self.device, "self_calibration_enabled", bool(enabled))

    def set_altitude(self, meters):
        if meters:
            self._safe(setattr, self.device, "altitude", int(meters))

    def set_ambient_pressure(self, hpa):
        if hpa:
            # SCD30 exposes ambient pressure as a settable property.
            self._safe(setattr, self.device, "ambient_pressure", int(hpa))

    def force_calibration(self, ref_ppm):
        try:
            # adafruit_scd30 uses a settable property for forced recalibration.
            self.device.forced_recalibration_reference = int(ref_ppm)
            return True
        except Exception as e:
            print("SCD30 force_calibration error:", e)
            return False

    # --- power / lifecycle primitives ---
    def _start_normal_raw(self):
        # SCD30 resumes continuous measurement after a reset on its own; if the
        # library exposes an explicit start, use it, otherwise this is a no-op.
        start = getattr(self.device, "start_continuous_measurements", None) \
            or getattr(self.device, "start_periodic_measurement", None)
        if start:
            start()
        return True

    def _stop_raw(self):
        stop = getattr(self.device, "stop_continuous_measurements", None) \
            or getattr(self.device, "stop_periodic_measurement", None)
        if stop:
            stop()

    def _soft_reset_raw(self):
        reset = getattr(self.device, "reset", None) \
            or getattr(self.device, "soft_reset", None)
        if reset:
            reset()
