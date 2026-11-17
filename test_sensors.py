from pathlib import Path
import sys
import types

# ---- Build fake adafruit_scd4x / adafruit_scd30 modules BEFORE import ----
calls = []

class FakeSCD4X:
    def __init__(self, i2c, present=True, variant=1):
        if not present:
            raise OSError("no device on bus")
        self._present = present
        self.sensor_variant = variant
        self.self_calibration_enabled = True
        self.altitude = 0
        self._co2 = 612
        # The real driver exposes this method on ALL parts; it is not identity.
        self.measure_single_shot = lambda: None
    @property
    def serial_number(self):
        return (0x12, 0xAB, 0x34, 0xCD, 0x56, 0x78)
    @property
    def data_ready(self): return True
    @property
    def CO2(self): return self._co2
    @property
    def temperature(self): return 22.5
    @property
    def relative_humidity(self): return 41.0
    def start_periodic_measurement(self): calls.append("4x.start_normal")
    def start_low_periodic_measurement(self): calls.append("4x.start_lp")
    def stop_periodic_measurement(self): calls.append("4x.stop")
    def reinit(self):
        self.stop_periodic_measurement()
        calls.append("4x.reinit")
    def set_ambient_pressure(self, hpa): calls.append(("4x.set_ap", hpa))
    def force_calibration(self, ppm):
        self.stop_periodic_measurement()
        calls.append(("4x.force_cal", ppm))
        return 0  # newer driver returns signed correction, including zero

class FakeSCD30:
    def __init__(self, i2c, present=True):
        if not present:
            raise OSError("no device on bus")
        self.self_calibration_enabled = True
        self.ambient_pressure = 0
        self.forced_recalibration_reference = 0
    @property
    def firmware_version(self): return (3, 66)
    @property
    def data_available(self): return True
    @property
    def CO2(self): return 700
    @property
    def temperature(self): return 23.0
    @property
    def relative_humidity(self): return 45.0
    def stop_continuous_measurements(self): calls.append("30.stop")
    def reset(self): calls.append("30.reset")

# scenario flags
SCENARIO = {"scd4x_present": True, "variant": 1, "scd30_present": True}

m4x = types.ModuleType("adafruit_scd4x")
m4x.SCD4X = lambda i2c: FakeSCD4X(i2c, present=SCENARIO["scd4x_present"], variant=SCENARIO["variant"])
m30 = types.ModuleType("adafruit_scd30")
m30.SCD30 = lambda i2c: FakeSCD30(i2c, present=SCENARIO["scd30_present"])
sys.modules["adafruit_scd4x"] = m4x
sys.modules["adafruit_scd30"] = m30

sys.path.insert(0, str(Path(__file__).resolve().parent))

def reload_pkg():
    for k in list(sys.modules):
        if k.startswith("knowco2"):
            del sys.modules[k]
    import knowco2.sensors as sensors
    return sensors

I2C = object()  # placeholder bus

print("=== Test 1: SCD41 present (preferred over SCD30) ===")
SCENARIO.update(scd4x_present=True, variant=1, scd30_present=True)
sensors = reload_pkg(); calls.clear()
s = sensors.detect_sensor(I2C)
assert s is not None and s.model == "SCD41", s.model
assert s.supports_low_power is True
assert s.read_serial() == "12AB34CD5678", s.read_serial()
assert calls == ["4x.start_normal"], calls   # started normal after reading serial
print("  model:", s.model, "serial:", s.read_serial(), "lp:", s.supports_low_power)
print("  data_ready:", s.data_ready, "read:", s.read())
assert s.data_ready and s.read() == (612, 22.5, 41.0)

print("=== Test 2: low-power switch (SCD41) ===")
calls.clear()
period = s.set_low_power(True)
assert period == 30.0, period
assert calls == ["4x.stop", "4x.start_lp"], calls
calls.clear()
period = s.set_low_power(False)
assert period == 5.0, period
assert calls == ["4x.stop", "4x.start_normal"], calls
print("  lp period:", 30.0, "normal period:", 5.0, "calls ok")

print("=== Test 3: recover() sequence (SCD41, low_power=True) ===")
calls.clear()
s.recover(low_power=True)
assert calls == ["4x.stop", "4x.reinit", "4x.start_lp"], calls
print("  recover calls:", calls)

print("=== Test 4: calibration/compensation dispatch ===")
calls.clear()
s.set_asc(False); s.set_altitude(100); s.set_ambient_pressure(1013); ok = s.force_calibration(420)
assert s.device.self_calibration_enabled is False
assert s.device.altitude == 100
assert ("4x.set_ap", 1013) in calls
assert ("4x.force_cal", 420) in calls and ok is True
print("  asc/alt/pressure/force_cal dispatched ok; force_cal ->", ok)

print("=== Test 5: SCD40 also supports low-power periodic mode ===")
SCENARIO.update(scd4x_present=True, variant=0, scd30_present=True)
sensors = reload_pkg(); calls.clear()
s = sensors.detect_sensor(I2C)
assert s.model == "SCD40" and s.supports_low_power is True, (s.model, s.supports_low_power)
calls.clear()
period = s.set_low_power(True)
assert period == 30.0 and calls == ["4x.stop", "4x.start_lp"], (period, calls)
print("  SCD40 low-power 30s; calls:", calls)

print("=== Test 6: fall back to SCD30 when no SCD4x ===")
SCENARIO.update(scd4x_present=False, variant=1, scd30_present=True)
sensors = reload_pkg(); calls.clear()
s = sensors.detect_sensor(I2C)
assert s.model == "SCD30", s.model
assert s.read_serial() == "FW3.66", s.read_serial()
assert s.data_ready and s.read() == (700, 23.0, 45.0)
ok = s.force_calibration(415)
assert ok is True and s.device.forced_recalibration_reference == 415
print("  model:", s.model, "serial:", s.read_serial(), "force_cal ok:", ok)

print("=== Test 7: no sensor at all ===")
SCENARIO.update(scd4x_present=False, variant=1, scd30_present=False)
sensors = reload_pkg()
s = sensors.detect_sensor(I2C)
assert s is None
print("  detect_sensor returned None as expected")

print("=== Test 8: SCD43 is identified explicitly through the registry ===")
SCENARIO.update(scd4x_present=True, variant=5, scd30_present=True)
sensors = reload_pkg(); calls.clear()
s = sensors.detect_sensor(I2C)
assert s.model == "SCD43" and s.supports_low_power is True
assert s.read_serial() == "12AB34CD5678"
assert s.data_ready and s.read() == (612, 22.5, 41.0)
assert s.set_low_power(True) == 30.0
s.recover(low_power=True)
assert s.force_calibration(420) is True

print("\nALL SENSOR-LAYER TESTS PASSED")
