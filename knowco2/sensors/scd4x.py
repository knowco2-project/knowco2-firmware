# knowco2/sensors/scd4x.py
# ----------------------------------------------------------------------
# Adapter for Sensirion SCD40 / SCD41 / SCD43 (adafruit_scd4x).
# SCD43 command compatibility is host-tested; hardware qualification is pending.
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

    def __init__(self, device):
        super().__init__(device)
        self._low_power = False
        self._idle_settings = {}
        self._ambient_pressure = None

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
            # All three parts share one Python class, including its single-shot
            # method. Method availability cannot identify the physical part.
            # All SCD4x variants support low-power periodic measurement.
            inst.supports_low_power = (
                callable(getattr(dev, "start_low_periodic_measurement", None))
            )
            # IMPORTANT: serial_number is readable only while idle, i.e.
            # BEFORE start_periodic_measurement(). This probes the bus too,
            # so a missing/dead sensor raises here and we return None.
            inst.serial = inst._read_serial_raw()
            try:
                variant = inst._read_variant_raw()
                inst.model = {0: "SCD40", 1: "SCD41", 5: "SCD43"}.get(variant, "SCD4x")
            except Exception as e:
                # An older part/driver or failed optional identity query must
                # not invent a model or disable otherwise working measurements.
                print("SCD4x variant unavailable:", e)
            inst._start_normal_raw()
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

    def _read_variant_raw(self):
        try:
            return self.device.sensor_variant
        except AttributeError:
            # Migration fallback: application-only OTA may leave 1.4.11 on a
            # device. Complete installs pin 1.4.13 and use the public property.
            # Remove this fallback only after the older library is retired.
            # _read_reply checks device._buffer, so use that exact buffer.
            self.device._send_command(0x202F, cmd_delay=0.001)
            self.device._read_reply(self.device._buffer, 3)
            return self.device._buffer[0] >> 4

    # --- calibration / compensation ---
    def set_asc(self, enabled):
        self._set_idle_setting("self_calibration_enabled", bool(enabled))

    def set_altitude(self, meters):
        if meters:
            self._set_idle_setting("altitude", int(meters))

    def _set_idle_setting(self, name, value):
        try:
            self._stop_raw()
            setattr(self.device, name, value)
            time.sleep(0.001)
            self._idle_settings[name] = value
        except Exception as e:
            print("SCD4x setting error:", name, e)
        finally:
            self._safe(self._resume_raw)

    def set_ambient_pressure(self, hpa):
        if hpa:
            try:
                self.device.set_ambient_pressure(int(hpa))
                time.sleep(0.001)
                self._ambient_pressure = int(hpa)
            except Exception as e:
                print("SCD4x pressure error:", e)

    def force_calibration(self, ref_ppm):
        success = False
        try:
            result = self.device.force_calibration(int(ref_ppm))
            # Migration fallback: 1.4.11 unpacks a signed word and compares it with 65535,
            # so its failure sentinel (-1 after unpacking) is never rejected.
            # Newer drivers return a signed correction; -1 ppm is valid there.
            if result is None:
                reply = self.device._buffer
                if reply[0] == 0xFF and reply[1] == 0xFF:
                    raise RuntimeError("sensor rejected forced calibration")
            success = True
        except Exception as e:
            print("SCD4x force_calibration error:", e)
        finally:
            # The vendor method leaves the sensor idle on success AND failure.
            if not self._safe(self._resume_raw):
                success = False
        return success

    # --- power / lifecycle primitives ---
    def _start_normal_raw(self):
        self.device.start_periodic_measurement()
        self._low_power = False
        return True

    def _start_low_power_raw(self):
        self.device.start_low_periodic_measurement()
        self._low_power = True
        return True

    def _resume_raw(self):
        if self._low_power:
            return self._start_low_power_raw()
        return self._start_normal_raw()

    def _stop_raw(self):
        self.device.stop_periodic_measurement()

    def _soft_reset_raw(self):
        self.device.reinit()  # includes stop and its 500 ms wait
        # 1.4.11 waits only 20 ms; allow the current datasheet's 30 ms maximum
        # before restoring volatile settings, which reinit reloads from EEPROM.
        time.sleep(0.03)
        for name, value in self._idle_settings.items():
            setattr(self.device, name, value)
            time.sleep(0.001)
        if self._ambient_pressure is not None:
            self.device.set_ambient_pressure(self._ambient_pressure)
            time.sleep(0.001)

    def recover(self, low_power=False):
        # Propagate a failed reset/configuration/restart to the application's
        # recovery handler instead of falsely reporting successful recovery.
        self._soft_reset_raw()
        if low_power and self.supports_low_power:
            self._start_low_power_raw()
        else:
            self._start_normal_raw()
