# knowco2/sensors/scd4x.py
# ----------------------------------------------------------------------
# Driver for Sensirion SCD40 / SCD41 (Adafruit adafruit_scd4x library).
#
# All SCD-4x-specific behaviour that used to be scattered across the main
# firmware (the start-before-vs-after-serial ordering, the data_ready
# property name, the low-power measurement call, the force_calibration
# call) lives here and nowhere else.
# ----------------------------------------------------------------------

import time

try:
    import adafruit_scd4x
except Exception:
    adafruit_scd4x = None

from .base import CO2Sensor


class SCD4xSensor(CO2Sensor):
    model = "SCD4x"
    normal_period_s = 5.0
    low_power_period_s = 30.0

    @classmethod
    def detect(cls, i2c):
        if adafruit_scd4x is None:
            return None
        try:
            dev = adafruit_scd4x.SCD4X(i2c)
        except Exception as e:
            print("SCD4x construct failed:", e)
            return None
        try:
            inst = cls(dev)
            # SCD41 exposes single-shot; SCD40 does not. Used only as a label.
            inst.model = "SCD41" if hasattr(dev, "measure_single_shot") else "SCD40"
            # Only the SCD41 has a genuine low-power periodic mode.
            inst.supports_low_power = (
                inst.model == "SCD41"
                and hasattr(dev, "start_low_power_periodic_measurement")
            )
            # IMPORTANT: serial_number is readable only while idle, i.e.
            # BEFORE start_periodic_measurement(). This probes the bus too,
            # so a missing/dead sensor raises here and we return None.
            inst.serial = inst._read_serial_raw()
            dev.start_periodic_measurement()
            return inst
        except Exception as e:
            print("SCD4x init failed:", e)
            return None

    # --- data ---
    @property
    def data_ready(self):
        return self.device.data_ready

    def read(self):
        return (self.device.CO2, self.device.temperature, self.device.relative_humidity)

    # --- identity ---
    def _read_serial_raw(self):
        return self._format_serial(getattr(self.device, "serial_number", None))

    # --- calibration / compensation ---
    def set_asc(self, enabled):
        self._safe(setattr, self.device, "self_calibration_enabled", bool(enabled))

    def set_altitude(self, meters):
        if meters:
            self._safe(setattr, self.device, "altitude", int(meters))

    def set_ambient_pressure(self, hpa):
        if hpa:
            self._safe(self.device.set_ambient_pressure, int(hpa))

    def force_calibration(self, ref_ppm):
        try:
            self.device.force_calibration(int(ref_ppm))
            return True
        except Exception as e:
            print("SCD4x force_calibration error:", e)
            return False

    # --- power / lifecycle primitives ---
    def _start_normal_raw(self):
        self.device.start_periodic_measurement()
        return True

    def _start_low_power_raw(self):
        self.device.start_low_power_periodic_measurement()
        return True

    def _stop_raw(self):
        self.device.stop_periodic_measurement()

    def _soft_reset_raw(self):
        self.device.soft_reset()
