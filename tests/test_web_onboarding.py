"""Regression tests for browser onboarding and raw HTTP transport."""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "knowco2" / "web"
I18N_DIR = WEB_DIR / "i18n"


class _Watchdog:
    def __init__(self):
        self.feeds = 0
        self.timeout = 20

    def feed(self):
        self.feeds += 1


def _load_http_util():
    """Load http_util without executing knowco2.web.__init__."""
    for name in (
        "knowco2.web.http_util",
        "knowco2.web",
        "knowco2.state",
        "knowco2.helpers",
        "knowco2",
    ):
        sys.modules.pop(name, None)

    knowco2_pkg = types.ModuleType("knowco2")
    knowco2_pkg.__path__ = [str(REPO_ROOT / "knowco2")]
    sys.modules["knowco2"] = knowco2_pkg

    web_pkg = types.ModuleType("knowco2.web")
    web_pkg.__path__ = [str(WEB_DIR)]
    sys.modules["knowco2.web"] = web_pkg

    state_mod = types.ModuleType("knowco2.state")
    state_mod._wd = _Watchdog()
    sys.modules["knowco2.state"] = state_mod

    helpers_mod = types.ModuleType("knowco2.helpers")
    helpers_mod.messages = []
    helpers_mod.log = lambda *args, **kwargs: helpers_mod.messages.append(args)
    sys.modules["knowco2.helpers"] = helpers_mod

    spec = importlib.util.spec_from_file_location(
        "knowco2.web.http_util",
        WEB_DIR / "http_util.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, state_mod, helpers_mod


def _load_i18n_package():
    """Load the real package initializer with a compact translations stub."""
    for name in (
        "knowco2.web.i18n.translations",
        "knowco2.web.i18n",
        "knowco2.web",
        "knowco2",
    ):
        sys.modules.pop(name, None)

    knowco2_pkg = types.ModuleType("knowco2")
    knowco2_pkg.__path__ = [str(REPO_ROOT / "knowco2")]
    sys.modules["knowco2"] = knowco2_pkg

    web_pkg = types.ModuleType("knowco2.web")
    web_pkg.__path__ = [str(WEB_DIR)]
    sys.modules["knowco2.web"] = web_pkg

    codes = (
        "en", "es", "fr", "de", "pt", "it", "nl", "sv", "pl", "cs",
        "ru", "uk", "tr", "vi", "id", "hi", "bn", "ta", "th", "ja",
        "zh", "ko",
    )
    translations_mod = types.ModuleType("knowco2.web.i18n.translations")
    translations_mod.TRANSLATIONS = {
        code: {"title": "KnowCO2 " + code, "save": "save " + code}
        for code in codes
    }
    sys.modules["knowco2.web.i18n.translations"] = translations_mod

    spec = importlib.util.spec_from_file_location(
        "knowco2.web.i18n",
        I18N_DIR / "__init__.py",
        submodule_search_locations=[str(I18N_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _RecvConn:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.timeout = None

    def recv(self, nbytes):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) <= nbytes:
            return chunk
        self.chunks.insert(0, chunk[nbytes:])
        return chunk[:nbytes]

    def settimeout(self, timeout):
        self.timeout = timeout


class _PartialSendConn:
    def __init__(self, max_write=7, eagain_first=False, fail=False):
        self.max_write = max_write
        self.eagain_first = eagain_first
        self.fail = fail
        self.calls = 0
        self.output = bytearray()

    def send(self, data):
        self.calls += 1
        if self.fail:
            raise OSError(32, "broken pipe")
        if self.eagain_first and self.calls == 1:
            raise OSError(11, "try again")
        count = min(self.max_write, len(data))
        self.output.extend(bytes(data[:count]))
        return count


class WebOnboardingRegressionTests(unittest.TestCase):
    def test_local_write_source_accepts_device_hosts(self):
        http, _, _ = _load_http_util()
        allowed = ("192.168.4.1", "192.168.1.42", "knowco2-abcd.local")
        for request in (
            b"POST / HTTP/1.1\r\nHost: 192.168.4.1\r\n\r\n",
            b"PATCH /api/v1/settings HTTP/1.1\r\nHost: 192.168.1.42:80\r\n\r\n",
            b"POST / HTTP/1.1\r\nHost: knowco2-abcd.local\r\nOrigin: http://knowco2-abcd.local\r\nSec-Fetch-Site: same-origin\r\n\r\n",
        ):
            self.assertTrue(http.request_source_allowed(request, allowed))

    def test_local_write_source_rejects_rebinding_and_cross_site(self):
        http, _, _ = _load_http_util()
        allowed = ("192.168.4.1", "knowco2-abcd.local")
        rejected = (
            b"POST / HTTP/1.1\r\n\r\n",
            b"POST / HTTP/1.1\r\nHost: attacker.example\r\n\r\n",
            b"POST / HTTP/1.1\r\nHost: 192.168.4.1\r\nOrigin: https://attacker.example\r\n\r\n",
            b"POST / HTTP/1.1\r\nHost: 192.168.4.1\r\nSec-Fetch-Site: cross-site\r\n\r\n",
        )
        for request in rejected:
            self.assertFalse(http.request_source_allowed(request, allowed))

    def test_local_route_security_regressions_are_closed(self):
        routes = (WEB_DIR / "routes.py").read_text(encoding="utf-8")
        settings = (REPO_ROOT / "knowco2" / "settings.py").read_text(encoding="utf-8")
        self.assertIn('elif route == "/":', routes)
        self.assertIn('Unknown local route', routes)
        self.assertIn('make_json_response(payload, cors=False)', routes)
        self.assertIn('if "admin_pw" in params and params["admin_pw"]:', settings)

    def test_i18n_is_a_real_package_initializer(self):
        self.assertTrue((I18N_DIR / "__init__.py").is_file())
        self.assertFalse(
            (I18N_DIR / "i18n_init.py").exists(),
            "stale i18n_init.py would compile to i18n_init.mpy, not __init__.mpy",
        )
        i18n = _load_i18n_package()
        options = i18n.build_lang_options("en")
        self.assertIn("<option value='en' selected>", options)
        translations_js = i18n.build_translations_js()
        self.assertTrue(translations_js.startswith("var T={"))
        self.assertIn('"en":', translations_js)

    def test_form_body_already_received_with_headers_is_preserved(self):
        http, _, _ = _load_http_util()
        body = b"sta_ssid=Office+WiFi&sta_password=secret123"
        request = (
            b"POST / HTTP/1.1\r\n"
            b"Host: 192.168.4.1\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" +
            body
        )
        self.assertEqual(http.read_request_body(_RecvConn(), request), body)

    def test_form_body_can_span_header_read_and_later_recv(self):
        http, _, _ = _load_http_util()
        body = b"sta_ssid=Office&sta_password=secret123"
        prefix = body[:12]
        remainder = body[12:]
        request = (
            b"POST / HTTP/1.1\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" +
            prefix
        )
        conn = _RecvConn((remainder[:5], remainder[5:]))
        self.assertEqual(http.read_request_body(conn, request), body)

    def test_lf_only_request_separator_is_supported(self):
        http, _, _ = _load_http_util()
        body = b"sta_ssid=Guest&sta_password="
        request = (
            b"POST / HTTP/1.1\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\n\n" +
            body
        )
        self.assertEqual(http.read_request_body(_RecvConn(), request), body)

    def test_ota_stream_keeps_body_prefix(self):
        http, _, _ = _load_http_util()
        body = b"PK\x03\x04example-update-data"
        prefix = body[:8]
        request = (
            b"POST /update?upload=1 HTTP/1.1\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" +
            prefix
        )
        conn = _RecvConn((body[8:],))
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "update.tmp")
            ok, message = http.stream_request_body_to_file(conn, request, dest)
            self.assertTrue(ok, message)
            self.assertEqual(Path(dest).read_bytes(), body)

    def test_send_all_handles_short_writes_and_eagain(self):
        http, state, _ = _load_http_util()
        payload = b"x" * 4097
        conn = _PartialSendConn(max_write=113, eagain_first=True)
        self.assertTrue(http.send_all(conn, payload))
        self.assertEqual(bytes(conn.output), payload)
        self.assertGreater(state._wd.feeds, 0)

    def test_send_all_reports_failure_instead_of_silent_truncation(self):
        http, _, helpers = _load_http_util()
        conn = _PartialSendConn(fail=True)
        self.assertFalse(http.send_all(conn, b"settings page"))
        self.assertTrue(helpers.messages)

    def test_error_status_has_correct_reason_phrase(self):
        http, _, _ = _load_http_util()
        header, body = http.build_response(
            500,
            "text/plain; charset=utf-8",
            b"error",
        )
        self.assertTrue(header.startswith(b"HTTP/1.1 500 Internal Server Error\r\n"))
        self.assertEqual(body, b"error")


if __name__ == "__main__":
    unittest.main()
