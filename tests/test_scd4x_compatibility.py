"""Host-only protocol tests: pinned Adafruit 1.4.11/1.4.13, simulated I2C.

No CircuitPython bytecode, physical sensor, accuracy or reliability is verified.
Run: python3 -m unittest discover -s tests -p test_scd4x_compatibility.py -v
"""
import hashlib
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def crc8(data):
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x31 if crc & 0x80 else 0)) & 0xFF
    return crc


def words(*values):
    reply = bytearray()
    for value in values:
        word = bytes((value >> 8, value & 255))
        reply.extend(word + bytes((crc8(word),)))
    return reply


class SimulatedSCD4x:
    """Enforces idle-only commands, execution delays, CRC and sample cadence."""
    def __init__(self, variant=0x5441):
        self.now = 0.0
        self.busy_until = 0.0
        self.mode = None
        self.period = 5.0
        self.next_sample = float("inf")
        self.variant = variant
        self.asc, self.altitude, self.pressure = 1, 0, 1013
        self.co2 = 612
        self.frc_word = 0x7FCE  # -50 ppm correction, NOT a failure
        self.reply = b""
        self.commands = []
        self.fail_command = None
        self.corrupt_command = None

    def sleep(self, seconds):
        self.now += seconds

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def write(self, data, *, end):
        data = bytes(data[:end])
        cmd = int.from_bytes(data[:2], "big")
        self.commands.append(cmd)
        if self.now + 1e-8 < self.busy_until:
            raise RuntimeError("command before execution delay elapsed")
        if cmd == self.fail_command:
            raise OSError("simulated NACK")
        if self.mode and cmd not in (0x3F86, 0xE4B8, 0xEC05, 0xE000):
            raise OSError("command requires idle")
        value = None
        if len(data) == 5:
            if crc8(data[2:4]) != data[4]:
                raise RuntimeError("bad write CRC")
            value = int.from_bytes(data[2:4], "big")
        delay = 0.001
        if cmd == 0x3F86:
            self.mode, delay = None, 0.5
        elif cmd in (0x21B1, 0x21AC):
            self.mode = cmd
            self.period = 5.0 if cmd == 0x21B1 else 30.0
            self.next_sample, delay = self.now + self.period, 0
        elif cmd == 0x3682:
            self.reply = words(0x12AB, 0x34CD, 0x5678)
        elif cmd == 0x202F:
            self.reply = words(self.variant)
        elif cmd == 0xE4B8:
            self.reply = words(int(bool(self.mode) and self.now >= self.next_sample))
        elif cmd == 0xEC05:
            if not self.mode or self.now < self.next_sample:
                raise OSError("sample not ready")
            self.reply = words(self.co2, 0x6666, 0x8000)
            self.next_sample = self.now + self.period
        elif cmd == 0x2416:
            self.asc = value
        elif cmd == 0x2313:
            self.reply = words(self.asc)
        elif cmd == 0x2427:
            self.altitude = value
        elif cmd == 0xE000:
            self.pressure = value
        elif cmd == 0x3646:
            self.asc, self.altitude, self.pressure = 1, 0, 1013
            delay = 0.03
        elif cmd == 0x362F:
            self.reply, delay = words(self.frc_word), 0.4
        else:
            raise AssertionError("unexpected command: 0x%04x" % cmd)
        if cmd == self.corrupt_command:
            self.reply[-1] ^= 1
        self.busy_until = self.now + delay

    def readinto(self, buffer, *, end):
        if self.now + 1e-8 < self.busy_until:
            raise RuntimeError("read before execution delay elapsed")
        if len(self.reply) != end:
            raise RuntimeError("unexpected reply size")
        buffer[:end] = self.reply


def load_pinned_driver(version):
    bus = types.ModuleType("adafruit_bus_device")
    def i2c_device(i2c, address):
        assert address == 0x62
        return i2c
    bus.i2c_device = types.SimpleNamespace(I2CDevice=i2c_device)
    modules = {
        "adafruit_bus_device": bus,
        "micropython": types.SimpleNamespace(const=lambda value: value),
        "busio": types.SimpleNamespace(I2C=object),
    }
    path = ROOT / ("tests/fixtures/adafruit_scd4x_" + version.replace(".", "_") + ".py")
    spec = importlib.util.spec_from_file_location("pinned_scd4x", path)
    driver = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(driver)
    return driver


class SCD4xCompatibilityTests(unittest.TestCase):
    driver_version = "1.4.11"
    source_sha256 = "4f7578a9cd4003c2e37f69e1887f574a6129e5d08883f3cc250e18d13ca5c827"

    @classmethod
    def setUpClass(cls):
        cls.driver = load_pinned_driver(cls.driver_version)
        import knowco2.sensors.scd4x as adapter
        cls.adapter = adapter

    def setUp(self):
        self.bus = SimulatedSCD4x()
        self.addCleanup(patch.stopall)
        patch("time.sleep", self.bus.sleep).start()
        patch.object(self.adapter, "adafruit_scd4x", self.driver).start()

    def detect(self):
        sensor = self.adapter.SCD4xSensor.detect(self.bus)
        self.assertIsNotNone(sensor)
        return sensor

    def test_vendor_source_fixture_has_reviewed_hash(self):
        source = ROOT / ("tests/fixtures/adafruit_scd4x_" + self.driver_version.replace(".", "_") + ".py")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         self.source_sha256)

    def test_newer_frc_signed_negative_one_is_valid(self):
        sensor = self.detect()
        def calibrate(target):
            sensor.device.stop_periodic_measurement()
            return -1
        patch.object(sensor.device, "force_calibration", calibrate).start()
        self.assertTrue(sensor.force_calibration(480))
        self.assertEqual(self.bus.mode, 0x21B1)

    def test_missing_low_power_api_falls_back_to_normal(self):
        patch.object(self.driver.SCD4X, "start_low_periodic_measurement", None).start()
        sensor = self.detect()
        self.assertFalse(sensor.supports_low_power)
        self.assertEqual(sensor.set_low_power(True), 5.0)
        self.assertEqual(self.bus.mode, 0x21B1)

    def test_low_power_start_failure_falls_back_to_normal(self):
        sensor = self.detect()
        self.bus.fail_command = 0x21AC
        self.assertEqual(sensor.set_low_power(True), 5.0)
        self.assertEqual(self.bus.mode, 0x21B1)

    def test_scd40_41_43_identification_ignores_reserved_bits(self):
        for word, model in ((0x0000, "SCD40"), (0x0440, "SCD40"),
                            (0x1440, "SCD41"), (0x5441, "SCD43"),
                            (0x5FFF, "SCD43")):
            with self.subTest(word=hex(word)):
                self.bus.variant = word
                sensor = self.detect()
                self.assertEqual(sensor.model, model)
                self.assertTrue(sensor.supports_low_power)
                self.assertEqual(sensor.read_serial(), "12AB34CD5678")
                self.assertEqual(self.bus.commands[-3:], [0x3682, 0x202F, 0x21B1])

    def test_shared_single_shot_method_is_not_model_detection(self):
        self.bus.variant = 0x0440
        sensor = self.detect()
        self.assertTrue(hasattr(sensor.device, "measure_single_shot"))
        self.assertEqual(sensor.model, "SCD40")
        self.assertNotIn(0x219D, self.bus.commands)

    def test_newer_driver_public_variant_property(self):
        class PublicVariantDriver(self.driver.SCD4X):
            @property
            def sensor_variant(self):
                return 5
        patch.object(self.driver, "SCD4X", PublicVariantDriver).start()
        sensor = self.detect()
        self.assertEqual(sensor.model, "SCD43")
        self.assertNotIn(0x202F, self.bus.commands)

    def test_unknown_variant_keeps_generic_label(self):
        self.bus.variant = 0xF123
        self.assertEqual(self.detect().model, "SCD4x")

    def test_variant_nack_keeps_generic_label_and_measurement(self):
        self.bus.fail_command = 0x202F
        self.assertEqual(self.detect().model, "SCD4x")
        self.assertEqual(self.bus.mode, 0x21B1)

    def test_variant_bad_crc_never_claims_scd43(self):
        self.bus.corrupt_command = 0x202F
        self.assertEqual(self.detect().model, "SCD4x")

    def test_serial_nack_rejects_sensor(self):
        self.bus.fail_command = 0x3682
        self.assertIsNone(self.adapter.SCD4xSensor.detect(self.bus))
        self.assertNotIn(0x21B1, self.bus.commands)

    def test_serial_bad_crc_rejects_sensor(self):
        self.bus.corrupt_command = 0x3682
        self.assertIsNone(self.adapter.SCD4xSensor.detect(self.bus))

    def test_normal_and_low_power_fresh_readings(self):
        sensor = self.detect()
        for low_power, period in ((False, 5.0), (True, 30.0), (False, 5.0)):
            self.assertEqual(sensor.set_low_power(low_power), period)
            self.assertFalse(sensor.data_ready)
            self.bus.sleep(period)
            self.assertTrue(sensor.data_ready)
            co2, temperature, humidity = sensor.read()
            self.assertEqual(co2, 612)
            self.assertAlmostEqual(temperature, 25.0)
            self.assertAlmostEqual(humidity, 50.00076, places=4)
            self.assertFalse(sensor.data_ready)

    def test_bad_measurement_crc_propagates(self):
        sensor = self.detect()
        self.bus.sleep(5)
        self.bus.corrupt_command = 0xEC05
        with self.assertRaisesRegex(RuntimeError, "CRC"):
            sensor.read()

    def test_asc_and_altitude_are_written_idle_then_resume_same_mode(self):
        sensor = self.detect()
        sensor.set_low_power(True)
        sensor.set_asc(False)
        sensor.set_altitude(100)
        sensor.set_ambient_pressure(1005)
        self.assertEqual((self.bus.asc, self.bus.altitude, self.bus.pressure), (0, 100, 1005))
        self.assertEqual(self.bus.mode, 0x21AC)

    def test_recovery_reinitializes_restores_settings_and_mode(self):
        sensor = self.detect()
        sensor.set_asc(False)
        sensor.set_altitude(100)
        sensor.set_ambient_pressure(1005)
        for low_power, command in ((True, 0x21AC), (False, 0x21B1)):
            self.bus.commands.clear()
            sensor.recover(low_power=low_power)
            self.assertIn(0x3646, self.bus.commands)
            self.assertEqual((self.bus.asc, self.bus.altitude, self.bus.pressure), (0, 100, 1005))
            self.assertEqual(self.bus.mode, command)
            self.bus.sleep(sensor.low_power_period_s if low_power else sensor.normal_period_s)
            self.assertTrue(sensor.data_ready)
            self.assertEqual(sensor.read()[0], 612)
            self.assertNotIn(0x3632, self.bus.commands)  # never erase calibration

    def test_recovery_failure_is_not_reported_as_success(self):
        sensor = self.detect()
        self.bus.fail_command = 0x3646
        with self.assertRaises(RuntimeError):
            sensor.recover()
        self.assertIsNone(self.bus.mode)

    def test_frc_success_and_failure_both_resume_previous_mode(self):
        sensor = self.detect()
        for low_power in (False, True):
            sensor.set_low_power(low_power)
            for raw, success in ((0x7FCE, True), (0x8000, True),
                                 (0x8032, True), (0xFFFF, False)):
                with self.subTest(low_power=low_power, raw=hex(raw)):
                    self.bus.sleep(180)
                    self.bus.frc_word = raw
                    self.assertIs(sensor.force_calibration(480), success)
                    self.assertEqual(self.bus.mode, 0x21AC if low_power else 0x21B1)

    def test_frc_bad_crc_fails_and_resumes(self):
        sensor = self.detect()
        self.bus.corrupt_command = 0x362F
        self.assertFalse(sensor.force_calibration(480))
        self.assertEqual(self.bus.mode, 0x21B1)

    def test_frc_restart_failure_is_not_success(self):
        sensor = self.detect()
        self.bus.fail_command = 0x21B1
        self.assertFalse(sensor.force_calibration(480))


class SCD4xReleasedDriverTests(SCD4xCompatibilityTests):
    driver_version = "1.4.13"
    source_sha256 = "711c28250f95a1ae569c9c30a89f33ea31c6c3878c3b106ce48b6e346dad4c7f"

    def test_released_driver_uses_public_api_without_legacy_frc_reply_access(self):
        sensor = self.detect()
        # The real vendor method owns its buffer. The adapter must use only
        # its returned correction and public start/stop APIs with this release.
        vendor_device = sensor.device
        class PublicOnly:
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AssertionError("private vendor access: " + name)
                return getattr(vendor_device, name)
        sensor.device = PublicOnly()
        sensor.device.stop_periodic_measurement()
        self.assertEqual(sensor._read_variant_raw(), 5)
        sensor._start_normal_raw()
        self.assertTrue(sensor.force_calibration(480))


if __name__ == "__main__":
    unittest.main()
