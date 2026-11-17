"""Configured customer-state tests using the real signed installer.

Synthetic secrets stay local. A fresh interpreter executes the real settings
loader. These are host tests, not FAT power-cut, sensor calibration or Wi-Fi
reconnection qualification. Rebooting the whole candidate is still a gate.
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_ota_staged as fixture
routes, ota = fixture.routes, fixture.ota

ROOT = Path(__file__).resolve().parents[1]

class CustomerStatePreservationTests(unittest.TestCase):
    def setUp(self):
        fixture.SignedInstallTests.setUpClass()
        self.device_fixture = fixture.SignedInstallTests(methodName='test_signed_install_readback_alias_removal_and_persistent_replay')
        self.addCleanup(self.device_fixture.doCleanups)
        self.device_fixture.setUp()
        self.f = self.device_fixture
        self.settings = {
            'device_id': 'TEST-KC2-preservation', 'sta_ssid': 'synthetic-wifi-1',
            'sta_password': 'synthetic-wifi-pass-1', 'sta_ssid_2': 'synthetic-wifi-2',
            'sta_password_2': 'synthetic-wifi-pass-2', 'sta_ssid_3': 'synthetic-wifi-3',
            'sta_password_3': 'synthetic-wifi-pass-3', 'ap_ssid': 'TEST-KC2-AP',
            'ap_password': 'synthetic-ap-pass', 'admin_password': 'synthetic-admin-pass',
            'cloud_enabled': True, 'cloud_api_url': 'https://example.invalid/v1/ingest',
            'cloud_device_id': 'synthetic-cloud-id', 'cloud_device_token': 'synthetic-cloud-token',
            'cloud_interval_sec': 120, 'mqtt_enabled': True, 'mqtt_broker': 'broker.invalid',
            'mqtt_port': 8883, 'mqtt_use_tls': True, 'mqtt_user': 'synthetic-user',
            'mqtt_pass': 'synthetic-mqtt-pass', 'aio_enabled': True, 'aio_username': 'synthetic-aio',
            'aio_key': 'synthetic-aio-key', 'temp_mode': 'C', 'display_mode': 2,
            'display_flip': True, 'lang': 'es', 'colorblind_mode': True,
            'low_threshold': 700, 'med_threshold': 1100, 'alert_threshold': 1700,
            'alerts_enabled': False, 'energy_mode': True, 'dim_enabled': True,
            'dim_start_hour': 20, 'dim_end_hour': 6, 'dim_brightness': 15,
            'asc_enabled': False, 'altitude': 123, 'ambient_pressure': 1002,
            'last_calibration_ts': 1700000000, 'last_calibration_ref': 450,
            'unknown_future_setting': {'retain': True},
        }
        raw = json.dumps(self.settings, sort_keys=True).encode()
        self.protected = {
            'settings.json': raw, 'settings.json.bak': raw,
            'knowco2-device.json': b'{"product_serial":"TEST-ONLY-identity","model":"KC2-01"}\n',
            'settings.toml': b'# synthetic user-owned file\nTEST_ONLY="retain"\n',
        }
        for name, data in self.protected.items(): (self.f.device / name).write_bytes(data)
        self.f.before = self.f.snapshot()

    def assert_preserved(self):
        for name, data in self.protected.items():
            path = self.f.device / name
            self.assertTrue(path.is_file(), 'protected file missing: ' + name)
            self.assertTrue(path.read_bytes() == data, 'protected bytes changed: ' + name)
        # Reload settings in a NEW interpreter so existing in-memory state cannot
        # make a lost-on-disk credential look preserved. Never print secret values.
        program = '''import json, sys, types
sys.path.insert(0, sys.argv[1])
sys.modules['storage'] = types.ModuleType('storage')
from knowco2 import settings, state
settings.SETTINGS_FILE = sys.argv[2]
expected = json.load(open(sys.argv[2], encoding='utf-8'))
state.settings = {}
settings.load_settings()
if any(state.settings.get(k) != v for k, v in expected.items()):
    raise SystemExit('settings reload mismatch (values redacted)')
if state.cloud_device_token != expected['cloud_device_token'] or state.temp_mode != 'C':
    raise SystemExit('runtime settings mismatch (values redacted)')
'''
        result = subprocess.run([sys.executable, '-c', program, str(ROOT), str(self.f.device/'settings.json')], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, 'fresh-process settings load failed (output redacted)')

    def test_success_preserves_setup_and_fresh_settings_load(self):
        self.assertTrue(self.f.install()[0]); self.assert_preserved()

    def test_two_distinct_updates_preserve_setup(self):
        self.assertTrue(self.f.install()[0])
        self.f.files['code.py'] = b'# distinct new application version 3\n'
        self.f.package(sequence=3)
        self.assertTrue(self.f.install()[0]); self.assert_preserved()

    def test_truncated_upload_preserves_setup_and_executables(self):
        self.f.path.write_bytes(self.f.path.read_bytes()[:100])
        self.assertFalse(self.f.install()[0])
        self.assertTrue(self.f.snapshot() == self.f.before)
        self.assert_preserved()

    def test_staged_readback_failure_preserves_setup(self):
        with patch.object(ota, 'verify_staged_file', side_effect=ota.OTARejected('injected readback failure')):
            self.assertFalse(self.f.install()[0])
        self.assert_preserved()

    def test_insufficient_space_preserves_setup_and_version_state(self):
        before = bytes(self.f.nvm)
        with patch.object(routes, '_fs_free_bytes', return_value=100000):
            self.assertFalse(self.f.install()[0])
        self.assertTrue(self.f.snapshot() == self.f.before)
        self.assertEqual(bytes(self.f.nvm), before)
        self.assert_preserved()

    def test_nvm_failure_preserves_setup(self):
        with patch.object(ota, 'reserve_version', side_effect=ota.OTARejected('injected NVM failure')):
            self.assertFalse(self.f.install()[0])
        self.assert_preserved()

    def test_pending_cannot_be_superseded_by_higher_sequence(self):
        plan = ota.verify_package(str(self.f.path))
        ota.reserve_version(plan)
        before = bytes(self.f.nvm)
        self.f.package(sequence=3)
        self.assertFalse(self.f.install()[0])
        self.assertEqual(bytes(self.f.nvm), before)
        self.f.package(sequence=2)
        self.assertTrue(self.f.install()[0]); self.assert_preserved()

    def test_signer_rejects_protected_user_paths(self):
        from tools import sign_ota
        for name in self.protected:
            files = dict(self.f.files); files[name] = b'overwrite'
            with self.subTest(path=name), self.assertRaises(ValueError):
                sign_ota.manifest_for(files, 2)
        self.assert_preserved()

if __name__ == '__main__': unittest.main(verbosity=2)
