"""Request-level browser-origin, method and OTA authorization regressions.

No filesystem writes or hardware are required. Authentication is exercised at
both the real socket dispatcher and the OTA handler boundary.
"""
import io
import time
import unittest
from unittest.mock import patch

from test_provisioning import (
    CaptureConn, RequestConn, OneClientServer,
    config, routes, state,
)
import test_provisioning as provisioning
from knowco2.web.http_util import request_source_allowed


class WebSecurityTests(unittest.TestCase):
    def setUp(self):
        # Reuse the hardware mocks and complete runtime-state fixture, not the
        # parent's tests. Each script runs in its own interpreter in CI.
        provisioning.ProvisioningTests.setUp(self)
        state.ip_str_cached = "192.168.4.1"
        state.settings["admin_password"] = "test-only-admin"

    def request(self, method="GET", path="/update", body=b"", headers=()):
        lines = [method + " " + path + " HTTP/1.1", "Host: 192.168.4.1"]
        lines.extend(headers)
        if body:
            lines.append("Content-Length: " + str(len(body)))
        conn = RequestConn(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        state.http_server_sock = OneClientServer(conn)
        routes.handle_http_client()
        return conn

    def test_ota_sta_requires_both_password_and_physical_unlock(self):
        state.wifi_mode = config.WIFI_MODE_STA
        for unlocked, password, expected in (
            (False, "", False), (False, "test-only-admin", False),
            (True, "", False), (True, "wrong", False),
            (True, "test-only-admin", True),
        ):
            state.ota_unlock_until = time.monotonic() + 300 if unlocked else 0
            self.assertEqual(routes.ota_authorized({"pw": password}), expected)
        state.settings["admin_password"] = ""
        self.assertFalse(routes.ota_authorized({"pw": ""}))
        state.wifi_mode = config.WIFI_MODE_AP
        self.assertTrue(routes.ota_authorized({}))

    def test_host_origin_and_referer_require_unambiguous_exact_http_origin(self):
        allowed = ("192.168.4.1", "knowco2-6677.local")
        bad_headers = (
            "Host: attacker.example", "Host: 192.168.4.1:81",
            "Host: 192.168.4.1/path", "Host: 192.168.4.1@attacker.example",
            "Host: 192.168.4.1\r\nHost: 192.168.4.1",
            "Host: 192.168.4.1\r\nOrigin: null",
            "Host: 192.168.4.1\r\nOrigin: https://192.168.4.1",
            "Host: 192.168.4.1\r\nOrigin: http://192.168.4.1:81",
            "Host: 192.168.4.1\r\nOrigin: http://knowco2-6677.local",
            "Host: 192.168.4.1\r\nOrigin: http://192.168.4.1/path",
            "Host: 192.168.4.1\r\nOrigin: http://192.168.4.1\r\nOrigin: http://attacker.example",
            "Host: 192.168.4.1\r\nReferer: https://attacker.example/page",
            "Host: 192.168.4.1\r\nSec-Fetch-Site: cross-site",
            "Host: 192.168.4.1\r\nSec-Fetch-Site: same-site",
            "Host: 192.168.4.1\r\nTransfer-Encoding: chunked",
            "Host: 192.168.4.1\r\nContent-Length: 1\r\nContent-Length: 2",
            "Host: 192.168.4.1\r\n X-Folded: forbidden",
        )
        for headers in bad_headers:
            request = ("POST / HTTP/1.1\r\n" + headers + "\r\n\r\n").encode()
            with self.subTest(headers=headers):
                self.assertFalse(request_source_allowed(request, allowed))
        for source in (
            "", "Origin: http://192.168.4.1:80\r\n",
            "Referer: http://192.168.4.1/settings\r\nSec-Fetch-Site: same-origin\r\n",
        ):
            self.assertTrue(request_source_allowed(
                ("POST / HTTP/1.1\r\nHost: 192.168.4.1\r\n" + source + "\r\n").encode(), allowed))

    def test_every_management_read_and_write_rejects_hostile_browser_source(self):
        paths = ("/", "/calibration", "/update", "/status", "/export.csv",
                 "/api/v1/info", "/api/v1/settings", "/api/v1/wifi",
                 "/api/v1/cloud/claim", "/api/v1/cloud/claim/clear",
                 "/api/v1/cloud/enabled", "/api/v1/cloud/credentials/forget",
                 "/api/v1/onboarding")
        for method in ("GET", "HEAD", "POST", "PATCH"):
            for path in paths:
                with self.subTest(method=method, path=path):
                    conn = self.request(method, path, headers=("Origin: https://attacker.example",))
                    self.assertIn(b"403 Forbidden", conn.output)
                    self.assertEqual(conn.response_json()["error"]["code"], "invalid_request_source")

    def test_query_credentials_rejected_even_with_body_and_physical_unlock(self):
        state.ota_unlock_until = time.monotonic() + 300
        for path in ("/?pw=test-only-admin", "/update?upload=1&pw=test-only-admin",
                     "/calibration?%70w=test-only-admin", "/api/v1/settings?admin_password=secret"):
            conn = self.request("POST", path, body=b"pw=test-only-admin")
            self.assertEqual(conn.response_json()["error"]["code"], "credentials_in_url")

    def test_raw_upload_uses_header_not_url_or_reflected_password(self):
        with patch.object(routes, "handle_update_route") as handler:
            self.request("POST", "/update?upload=1", body=b"ZIP",
                         headers=("X-KnowCO2-Admin-Password: test-only-admin",))
            self.assertEqual(handler.call_args.args[1]["pw"], "test-only-admin")
        conn = CaptureConn()
        routes.handle_update_route(conn, {"pw": "never-render-this-secret"})
        self.assertNotIn(b"never-render-this-secret", conn.output)
        self.assertIn(b"X-KnowCO2-Admin-Password", conn.output)
        self.assertNotIn(b"&pw=", conn.output)
        self.assertNotIn(b"name='firmware_url'", conn.output)

    def test_sta_unauthorized_upload_is_rejected_before_streaming(self):
        state.wifi_mode = config.WIFI_MODE_STA
        with patch.object(routes, "stream_request_body_to_file") as stream:
            conn = self.request("POST", "/update?upload=1", body=b"ZIP",
                                headers=("X-KnowCO2-Admin-Password: test-only-admin",))
            self.assertIn(b"403 Forbidden", conn.output)
            stream.assert_not_called()

    def test_url_download_and_unsupported_methods_are_rejected(self):
        conn = self.request("POST", "/update", body=b"firmware_url=http%3A%2F%2Fattacker.example%2Fcode.py")
        self.assertEqual(conn.response_json()["error"]["code"], "signed_upload_required")
        for method, path in (("GET", "/update?upload=1"), ("HEAD", "/update?firmware_url=x"),
                             ("PATCH", "/"), ("PATCH", "/calibration"),
                             ("POST", "/status"), ("POST", "/export.csv")):
            with self.subTest(method=method, path=path):
                self.assertIn(b"405 Method Not Allowed", self.request(method, path).output)

    def test_oversize_or_empty_upload_rejected_before_streaming(self):
        for length in (0, 512 * 1024 + 1, -1):
            with patch.object(routes, "stream_request_body_to_file") as stream:
                conn = self.request("POST", "/update?upload=1", headers=("Content-Length: " + str(length),))
                self.assertIn(b"413 Payload Too Large", conn.output)
                stream.assert_not_called()

    def test_plain_python_upload_does_not_install(self):
        conn = CaptureConn()
        with patch("builtins.open", side_effect=lambda *a, **k: io.BytesIO(b"# arbitrary Python")), \
             patch("os.remove"), patch.object(routes, "_fs_junk_cleanup", return_value=0), \
             patch.object(routes, "_fs_free_bytes", return_value=1000000), \
             patch.object(routes, "stream_request_body_to_file", return_value=(True, "OK")), \
             patch.object(routes, "_process_zip_update") as install:
            routes.handle_update_route(conn, {"upload": "1"}, method=b"POST",
                                       raw_headers=b"POST /update HTTP/1.1\r\nContent-Length: 20\r\n\r\n")
            install.assert_not_called()
            self.assertIn(b"Only signed OTA ZIP", conn.output)

    def test_truncated_form_never_mutates(self):
        with patch.object(routes, "handle_root_route") as root:
            conn = self.request("POST", "/", headers=("Content-Length: 100",))
            self.assertEqual(conn.response_json()["error"]["code"], "incomplete_body")
            root.assert_not_called()


if __name__ == "__main__":
    unittest.main()
