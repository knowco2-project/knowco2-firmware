#!/usr/bin/env python3
"""Hardware-free tests for Local API v1 and cloud activation exchange."""

import json
import os
import sys
import types
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

for name in ("storage", "socketpool", "mdns", "microcontroller", "rtc"):
    sys.modules.setdefault(name, types.ModuleType(name))

wifi_mock = types.ModuleType("wifi")
wifi_mock.radio = types.SimpleNamespace(connected=True)
sys.modules["wifi"] = wifi_mock

from knowco2 import config, state
from knowco2.telemetry import cloud
from knowco2.web import routes

VALID_DEVICE_SECRET = "U1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1M="


class CaptureConn:
    def __init__(self):
        self.output = bytearray()

    def send(self, data):
        self.output.extend(bytes(data))
        return len(data)

    def response_json(self):
        raw = bytes(self.output)
        return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))

    def close(self):
        pass


class RequestConn(CaptureConn):
    def __init__(self, request):
        super().__init__()
        self.request = request

    def recv(self, size):
        if not self.request:
            return b""
        chunk = self.request[:size]
        self.request = self.request[size:]
        return chunk


class OneClientServer:
    def __init__(self, conn):
        self.conn = conn

    def accept(self):
        return self.conn, ("192.168.4.2", 12345)


class Response:
    def __init__(self, status, payload, falsey=False):
        self.status_code = status
        self.text = json.dumps(payload) if isinstance(payload, dict) else payload
        self.closed = False
        self.falsey = falsey

    def close(self):
        self.closed = True

    def __bool__(self):
        return not self.falsey


class Session:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data, headers, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        state.settings = {
            "device_id": "KC2-2026-0001-7",
            "serial_number": "KC2-2026-0001-7",
            "sta_ssid": "",
            "sta_password": "",
            "cloud_device_id": "",
            "cloud_device_token": "",
            "cloud_api_url": "",
            "cloud_enabled": False,
        }
        state.hwid_hex = "0011223344556677"
        state.board_id_str = "adafruit_feather_esp32s3_reverse_tft"
        state.mdns_hostname = "knowco2-6677"
        state.wifi_mode = config.WIFI_MODE_AP
        state.cloud_device_id = ""
        state.cloud_device_token = ""
        state.cloud_enabled = False
        state.pending_cloud_claim = None
        state.cloud_activation_request_id = None
        state.cloud_activation_state = "unconfigured"
        state.cloud_activation_error = ""
        state.cloud_activation_failures = 0
        state.cloud_activation_next_attempt = 0.0
        state.onboarding_connect_after = 0.0
        state.ota_unlock_until = 0.0
        state._sta_fallback = False
        state._sta_auto_retry_count = 0
        state.last_sta_auto_retry = 0.0
        state._wd = None

        routes.settings_mod.save_settings = lambda: True

        def apply_settings():
            state.cloud_device_id = state.settings.get("cloud_device_id", "")
            state.cloud_device_token = state.settings.get("cloud_device_token", "")
            state.cloud_api_url = state.settings.get("cloud_api_url", "")
            state.cloud_enabled = bool(state.settings.get("cloud_enabled", False))

        routes.settings_mod.apply_settings = apply_settings
        cloud.settings_mod.save_settings = lambda: True
        cloud.settings_mod.apply_settings = apply_settings
        cloud.wifi_mod.ensure_sta_connected = lambda: True
        cloud.idf_heap_info = lambda: (120000, 80000)
        cloud.runtime.show_status = lambda *args, **kwargs: None
        routes.runtime.show_busy = lambda *args, **kwargs: None
        routes.runtime.clear_busy = lambda *args, **kwargs: None
        routes.runtime.update_visibility = lambda *args, **kwargs: None
        routes.runtime.refresh_text = lambda *args, **kwargs: None

    def test_info_is_secret_free_and_has_no_legacy_pair_code(self):
        state.settings["sta_password"] = "wifi-secret"
        state.settings["cloud_device_token"] = "cloud-secret"
        state.pending_cloud_claim = {"claim_code": "ABCDEFGH"}
        state.cloud_activation_request_id = "F" * 64
        conn = CaptureConn()
        routes.handle_api_info_route(conn)
        body = bytes(conn.output).lower()
        self.assertNotIn(b"wifi-secret", body)
        self.assertNotIn(b"cloud-secret", body)
        self.assertNotIn(b"abcdefgh", body)
        self.assertNotIn(b"pair_code", body)
        self.assertNotIn(b"0011223344556677", body)
        self.assertEqual(conn.response_json()["cloud"]["activation_state"], "pending")

    def test_primary_portal_has_fast_path_without_permanent_secret_input(self):
        html = routes.portal_page.render_settings_page()
        self.assertIn('id="onboarding-form"', html)
        self.assertIn("/api/v1/onboarding", html)
        self.assertNotIn('name="cloud_device_token"', html)
        self.assertNotIn('id="status-pair"', html)
        self.assertNotIn("Pair code", html)

    def test_composite_onboarding_is_write_only_and_defers_sta(self):
        request = {
            "wifi": {"ssid": "Factory WiFi", "password": "wifi-secret"},
            "cloud": {"claim_code": "abcd-efgh"},
            "connect": True,
        }
        conn = CaptureConn()
        routes.handle_api_onboarding_route(conn, b"POST", request)
        response_bytes = bytes(conn.output).lower()
        self.assertNotIn(b"factory wifi", response_bytes)
        self.assertNotIn(b"wifi-secret", response_bytes)
        self.assertNotIn(b"abcdefgh", response_bytes)
        self.assertEqual(state.settings["sta_ssid"], "Factory WiFi")
        self.assertEqual(state.pending_cloud_claim, {"claim_code": "ABCDEFGH"})
        self.assertEqual(len(state.cloud_activation_request_id), 64)
        self.assertGreater(state.onboarding_connect_after, 0)
        self.assertTrue(conn.response_json()["connection_scheduled"])

    def test_sta_mutation_requires_admin_and_physical_unlock(self):
        state.wifi_mode = config.WIFI_MODE_STA
        state.settings["admin_password"] = "admin-secret"
        request = {"ssid": "Home", "password": "wifi-secret"}
        conn = CaptureConn()
        routes.handle_api_wifi_route(conn, b"POST", request)
        self.assertEqual(conn.response_json()["error"]["code"], "physical_setup_required")
        self.assertEqual(state.settings["sta_ssid"], "")

    def test_legacy_form_cannot_mutate_on_get_or_unlocked_sta(self):
        conn = CaptureConn()
        routes.handle_root_route(
            conn,
            {"sta_ssid": "attacker", "sta_password": "attacker-secret"},
            method=b"GET",
        )
        self.assertEqual(state.settings["sta_ssid"], "")

        state.wifi_mode = config.WIFI_MODE_STA
        conn = CaptureConn()
        routes.handle_root_route(
            conn,
            {"sta_ssid": "attacker", "sta_password": "attacker-secret"},
            method=b"POST",
        )
        self.assertEqual(conn.response_json()["error"]["code"], "physical_setup_required")
        self.assertEqual(state.settings["sta_ssid"], "")

    def test_calibration_get_cannot_mutate_and_sta_write_is_gated(self):
        state.settings["altitude"] = 0
        conn = CaptureConn()
        routes.handle_calibration_route(
            conn, {"update": "1", "altitude": "2000"}, method=b"GET"
        )
        self.assertEqual(state.settings["altitude"], 0)
        self.assertNotIn(b"type=\"hidden\" name=\"pw\"", conn.output)

        state.wifi_mode = config.WIFI_MODE_STA
        conn = CaptureConn()
        routes.handle_calibration_route(
            conn, {"update": "1", "altitude": "2000"}, method=b"POST"
        )
        self.assertEqual(conn.response_json()["error"]["code"], "physical_setup_required")
        self.assertEqual(state.settings["altitude"], 0)

    def test_router_returns_json_404_and_accepts_patch(self):
        missing = RequestConn(b"GET /api/v1/missing HTTP/1.1\r\nHost: device\r\n\r\n")
        state.http_server_sock = OneClientServer(missing)
        routes.handle_http_client()
        self.assertIn(b"HTTP/1.1 404 Not Found", missing.output)
        self.assertEqual(missing.response_json()["error"]["code"], "not_found")

        body = json.dumps({"alerts_enabled": False}).encode("utf-8")
        request = (
            b"PATCH /api/v1/settings HTTP/1.1\r\n"
            b"Host: 192.168.4.1\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        )
        patch_conn = RequestConn(request)
        state.http_server_sock = OneClientServer(patch_conn)
        routes.handle_http_client()
        self.assertIn(b"HTTP/1.1 200 OK", patch_conn.output)
        self.assertIs(state.settings["alerts_enabled"], False)

    def test_activation_preserves_serial_and_installs_cloud_namespace(self):
        state.wifi_mode = config.WIFI_MODE_STA
        state.pending_cloud_claim = {"claim_code": "ABCDEFGH"}
        state.cloud_activation_request_id = "A" * 64
        response = Response(200, {
            "schema_version": 1,
            "device_id": "cloud-device-42",
            "device_secret": VALID_DEVICE_SECRET,
            "cloud_api_url": "https://api.knowco2.com",
        })
        session = Session([response])
        cloud._get_session = lambda: session

        self.assertTrue(cloud.activate_pending_claim())
        self.assertEqual(state.settings["device_id"], "KC2-2026-0001-7")
        self.assertEqual(state.settings["cloud_device_id"], "cloud-device-42")
        self.assertEqual(state.settings["cloud_device_token"], VALID_DEVICE_SECRET)
        self.assertIsNone(state.pending_cloud_claim)
        self.assertIsNone(state.cloud_activation_request_id)
        sent = json.loads(session.calls[0][1])
        self.assertEqual(sent["serial"], "KC2-2026-0001-7")
        self.assertEqual(sent["request_id"], "A" * 64)
        self.assertEqual(session.calls[0][0], "https://api.knowco2.com/v1/devices/activate")
        self.assertTrue(response.closed)

    def test_activation_retry_reuses_nonce_and_falsey_rejection_closes(self):
        state.wifi_mode = config.WIFI_MODE_STA
        state.pending_cloud_claim = {"activation_token": "T" * 40}
        state.cloud_activation_request_id = "B" * 64
        terminal = Response(410, {"error": "expired"}, falsey=True)
        session = Session([OSError(113, "unreachable"), terminal])
        cloud._get_session = lambda: session

        self.assertFalse(cloud.activate_pending_claim())
        state.cloud_activation_next_attempt = 0.0
        self.assertFalse(cloud.activate_pending_claim())
        first = json.loads(session.calls[0][1])
        second = json.loads(session.calls[1][1])
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertIsNone(state.pending_cloud_claim)
        self.assertIsNone(state.cloud_activation_request_id)
        self.assertTrue(terminal.closed)

    def test_invalid_200_keeps_claim_and_nonce_for_redelivery(self):
        state.wifi_mode = config.WIFI_MODE_STA
        state.pending_cloud_claim = {"claim_code": "ABCDEFGH"}
        state.cloud_activation_request_id = "C" * 64
        response = Response(200, "truncated-json")
        session = Session([response])
        cloud._get_session = lambda: session

        self.assertFalse(cloud.activate_pending_claim())
        self.assertEqual(state.pending_cloud_claim, {"claim_code": "ABCDEFGH"})
        self.assertEqual(state.cloud_activation_request_id, "C" * 64)
        self.assertEqual(state.cloud_activation_error, "invalid_cloud_response")
        self.assertGreater(state.cloud_activation_next_attempt, 0)
        self.assertTrue(response.closed)

    def test_main_loop_syncs_time_before_first_activation_attempt(self):
        with open(os.path.join(REPO_ROOT, "code.py"), "r", encoding="utf-8") as source:
            loop = source.read()
        ntp_call = loop.index('_busy("ntp", ntp_mod.ntp_sync, False)')
        activation_call = loop.index('_busy("pair", cloud_mod.activate_pending_claim)')
        self.assertLess(ntp_call, activation_call)
        self.assertIn("state.ntp_synced and state.pending_cloud_claim", loop)
        with open(
            os.path.join(REPO_ROOT, "knowco2", "net", "wifi.py"),
            "r",
            encoding="utf-8",
        ) as source:
            wifi_source = source.read()
        self.assertIn(
            "state.last_ntp_attempt = time.monotonic() - config.NTP_MIN_RETRY_S",
            wifi_source,
        )


if __name__ == "__main__":
    unittest.main()
