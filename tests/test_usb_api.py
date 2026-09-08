#!/usr/bin/env python3
"""Hardware-free contract tests for the KnowCO2 USB data API."""

import importlib
import json
import os
import sys
import types
import unittest

# CI runs this file with tests/ as the working directory. Add the repository
# root explicitly so the in-tree knowco2 package is importable in the same way
# as the older standalone firmware tests.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class FakePort:
    def __init__(self):
        self.connected = True
        self.timeout = None
        self.write_timeout = None
        self.rx = bytearray()
        self.tx = bytearray()

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, count):
        data = bytes(self.rx[:count])
        del self.rx[:count]
        return data

    def write(self, data):
        self.tx.extend(data)
        return len(data)


class UsbApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = FakePort()
        usb_cdc = types.ModuleType("usb_cdc")
        usb_cdc.data = cls.port
        sys.modules["usb_cdc"] = usb_cdc

        from knowco2 import state, version
        cls.state = state
        cls.version = version
        state.settings.clear()
        state.settings.update({
            "device_id": "test-node",
            "admin_password": "secret-never-export",
            "sta_ssid": "private-network",
            "sta_password": "private-password",
            "cloud_device_token": "private-token",
            "asc_enabled": True,
            "altitude": 12,
            "ambient_pressure": 0,
        })
        state.hwid_hex = "AABBCCDDEEFF"
        state.board_id_str = "board-test"
        state.pair_code = "PAIR1234"
        state.sensor_model_str = "SCD41"
        state.scd_serial_str = "SCDSERIAL"
        state.last_co2 = 612
        state.last_temp_c = 22.5
        state.last_rh = 44.0
        state.rate_of_change = 0.2
        state.cached_vbat = 4.0
        state.cached_pct = 88
        state.last_scd_sample_ts = 100.0
        state.co2_history[:] = [500, 550, 612]
        state.ntp_synced = False

        sys.modules.pop("knowco2.usb_api", None)
        cls.api = importlib.import_module("knowco2.usb_api")

    def setUp(self):
        self.port.rx[:] = b""
        self.port.tx[:] = b""
        self.api._port = self.port
        self.api._was_connected = True
        self.api._reset_session()
        self.api._was_connected = True

    def request(self, method, params=None, req_id=1):
        message = {
            "v": 1,
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        self.port.rx.extend((json.dumps(message) + "\n").encode())
        for _ in range(8):
            self.api.poll()
        lines = [line for line in self.port.tx.decode().splitlines() if line]
        self.assertTrue(lines)
        return json.loads(lines[-1])

    def test_hello_identifies_knowco2_protocol(self):
        response = self.request("hello")
        self.assertTrue(response["ok"])
        result = response["result"]
        self.assertEqual(result["protocol_version"], 1)
        self.assertEqual(result["transport"], "usb-cdc-ndjson")
        self.assertEqual(result["hwid"], "AABBCCDDEEFF")
        self.assertEqual(result["sensor_model"], "SCD41")

    def test_status_never_exports_credentials(self):
        response = self.request("status.get")
        encoded = json.dumps(response)
        self.assertNotIn("secret-never-export", encoded)
        self.assertNotIn("private-password", encoded)
        self.assertNotIn("private-token", encoded)
        self.assertNotIn("private-network", encoded)
        self.assertEqual(response["result"]["sample"]["co2_ppm"], 612)

    def test_settings_get_is_public_only(self):
        response = self.request("settings.get")
        settings = response["result"]
        self.assertNotIn("admin_password", settings)
        self.assertNotIn("sta_password", settings)
        self.assertNotIn("cloud_device_token", settings)
        self.assertIn("asc_enabled", settings)

    def test_history_is_bounded(self):
        response = self.request("history.get", {"limit": 2})
        self.assertEqual(response["result"]["co2_ppm"], [550, 612])

    def test_unknown_method_is_explicit_error(self):
        response = self.request("dangerous.magic")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "method_not_found")

    def test_oversized_frame_is_rejected_without_crashing(self):
        self.port.rx.extend(b"x" * (self.api.MAX_RX_LINE + 10) + b"\n")
        for _ in range(8):
            self.api.poll()
        text = self.port.tx.decode()
        self.assertIn("frame_too_large", text)


if __name__ == "__main__":
    unittest.main()
