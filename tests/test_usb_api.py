#!/usr/bin/env python3
"""Hardware-free contract tests for the KnowCO2 USB data API."""

import ast
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

    def _assert_identity_ignores_legacy_pair_code(self, method, present):
        # The real state module has no pair_code. Exercise that case explicitly,
        # and also ensure a stale legacy value is never exposed over USB.
        had_attribute = hasattr(self.state, "pair_code")
        previous = getattr(self.state, "pair_code", None)
        sentinel = "legacy-pair-secret-never-export"
        try:
            if present:
                self.state.pair_code = sentinel
            elif had_attribute:
                delattr(self.state, "pair_code")
            self.assertEqual(hasattr(self.state, "pair_code"), present)
            response = self.request(method)
        finally:
            if had_attribute:
                self.state.pair_code = previous
            elif hasattr(self.state, "pair_code"):
                delattr(self.state, "pair_code")

        self.assertTrue(response.get("ok"), response)
        result = response["result"]
        identity = result if method == "hello" else result["identity"]
        self.assertEqual(identity["hwid"], "AABBCCDDEEFF")
        self.assertNotIn("pair_code", identity)
        self.assertNotIn(sentinel, json.dumps(response))

    def test_hello_without_legacy_pair_code(self):
        self._assert_identity_ignores_legacy_pair_code("hello", False)

    def test_status_without_legacy_pair_code(self):
        self._assert_identity_ignores_legacy_pair_code("status.get", False)

    def test_hello_never_exports_legacy_pair_code(self):
        self._assert_identity_ignores_legacy_pair_code("hello", True)

    def test_status_never_exports_legacy_pair_code(self):
        self._assert_identity_ignores_legacy_pair_code("status.get", True)

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


class CircuitPythonRxBuffer(bytearray):
    """Model the deletion restriction observed on physical CircuitPython 10.3.2.

    This is NOT a complete CircuitPython emulator. Native CPython bytearray
    would otherwise let the USB parser's unsupported `del` pass host tests.
    """

    def __delitem__(self, key):
        raise TypeError("'bytearray' object doesn't support item deletion")


class UsbReceiveCompatibilityTests(unittest.TestCase):
    """Exercise the real receive function in isolation with a restricted buffer.

    Extracting the function from the checked-out source avoids copying its
    implementation into the test or importing unrelated hardware modules.
    The original protocol tests above continue to test the complete USB API.
    """

    @classmethod
    def setUpClass(cls):
        source_path = os.path.join(REPO_ROOT, "knowco2", "usb_api.py")
        with open(source_path, encoding="utf-8") as source:
            tree = ast.parse(source.read(), filename=source_path)
        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == "_read_and_dispatch"]
        if len(functions) != 1:
            raise AssertionError("Expected one production USB receive function")
        names = {"MAX_RX_BUFFER", "MAX_RX_LINE", "MAX_READ_PER_POLL",
                 "MAX_COMMANDS_PER_POLL"}
        cls.limits = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in names:
                        cls.limits[target.id] = ast.literal_eval(node.value)
        if set(cls.limits) != names:
            raise AssertionError("USB receive limits must be available to tests")
        cls.receiver_code = compile(
            ast.Module(body=functions, type_ignores=[]), source_path, "exec")

    def setUp(self):
        self.port = FakePort()
        self.buffer = CircuitPythonRxBuffer()
        self.lines = []
        self.events = []
        self.environment = dict(self.limits)
        self.environment.update({
            "_port": self.port,
            "_rx": self.buffer,
            "_handle_line": self.lines.append,
            "_queue_event": lambda name, data: self.events.append((name, data)),
            "_error": lambda code, message: {"code": code, "message": message},
        })
        exec(self.receiver_code, self.environment)
        self.receive = self.environment["_read_and_dispatch"]

    def feed(self, data):
        self.port.rx.extend(data)
        # Always bounded, even if a future parser change stops consuming input.
        for _ in range(1 + len(data) // self.limits["MAX_READ_PER_POLL"]):
            self.receive()

    def test_buffer_rejects_deletion_but_accepts_slice_assignment(self):
        self.buffer.extend(b"hello\ntail")
        with self.assertRaises(TypeError):
            del self.buffer[:6]
        self.buffer[:6] = b""
        self.assertEqual(bytes(self.buffer), b"tail")

    def test_hello_is_consumed_before_dispatch(self):
        frame = b'{"v":1,"id":"verify-1","method":"hello","params":{}}'
        def on_line(line):
            self.assertIs(self.environment["_rx"], self.buffer)
            self.assertEqual(bytes(self.buffer), b"")
            self.lines.append(line)
        self.environment["_handle_line"] = on_line
        self.feed(frame + b"\n")
        self.assertEqual(self.lines, [frame])
        self.assertEqual(self.events, [])

    def test_fragmented_request_waits_for_newline(self):
        self.feed(b'{"method":"hel')
        self.assertEqual(self.lines, [])
        self.feed(b'lo"}\n')
        self.assertEqual(self.lines, [b'{"method":"hello"}'])
        self.assertEqual(bytes(self.buffer), b"")

    def test_multiple_frames_preserve_incomplete_tail(self):
        self.feed(b"first\nsecond\ntai")
        self.assertEqual(self.lines, [b"first", b"second"])
        self.assertEqual(bytes(self.buffer), b"tai")
        self.feed(b"l\n")
        self.assertEqual(self.lines, [b"first", b"second", b"tail"])

    def test_command_budget_remains_bounded(self):
        limit = self.limits["MAX_COMMANDS_PER_POLL"]
        self.port.rx.extend(b"a\n" * (limit + 1))
        self.receive()
        self.assertEqual(self.lines, [b"a"] * limit)
        self.assertEqual(bytes(self.buffer), b"a\n")
        self.receive()
        self.assertEqual(self.lines, [b"a"] * (limit + 1))

    def test_crlf_is_normalized(self):
        self.feed(b"hello\r\n")
        self.assertEqual(self.lines, [b"hello"])
        self.assertEqual(bytes(self.buffer), b"")

    def test_oversized_frame_does_not_poison_next_request(self):
        self.feed(b"x" * (self.limits["MAX_RX_LINE"] + 1) + b"\nhello\n")
        self.assertEqual(self.lines, [b"hello"])
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][1]["code"], "frame_too_large")
        self.assertEqual(bytes(self.buffer), b"")

    def test_overflow_clears_buffer_and_next_request_works(self):
        self.feed(b"x" * (self.limits["MAX_RX_BUFFER"] + 1))
        self.assertEqual(bytes(self.buffer), b"")
        self.assertEqual(self.lines, [])
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][1]["code"], "rx_overflow")
        self.feed(b"hello\n")
        self.assertEqual(self.lines, [b"hello"])

    def test_disabled_data_endpoint_is_a_noop(self):
        self.environment["_port"] = None
        self.receive()
        self.assertEqual(self.lines, [])
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
