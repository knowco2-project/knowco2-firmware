#!/usr/bin/env python3
"""Security regression suite (RC-55).

Every test here pins a hole that was found in review and closed. They run the
REAL firmware modules on desktop CPython with the same hardware-free mocks as
test_provisioning.py. Nothing in this file writes outside a temporary
directory: install paths that touch the device root are never reached.
"""

import builtins
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

for name in ("storage", "socketpool", "mdns", "microcontroller", "rtc"):
    sys.modules.setdefault(name, types.ModuleType(name))

wifi_mock = types.ModuleType("wifi")
wifi_mock.radio = types.SimpleNamespace(connected=True)
sys.modules["wifi"] = wifi_mock

_battery = types.ModuleType("knowco2.battery")
_battery.read = lambda: (4.0, 80)
sys.modules["knowco2.battery"] = _battery

from knowco2 import access, config, crypto, helpers, state
from knowco2 import settings as settings_mod
from knowco2.net import ntp
from knowco2.telemetry import cloud
from knowco2.web import http_util, routes

import knowco2
knowco2.battery = _battery

_REAL_SAVE = settings_mod.save_settings
_REAL_APPLY = settings_mod.apply_settings

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) "
      "Gecko/20100101 Firefox/140.0")


class Conn:
    def __init__(self, request=b""):
        self.request = request
        self.output = bytearray()

    def recv(self, size):
        chunk, self.request = self.request[:size], self.request[size:]
        return chunk

    def send(self, data):
        self.output.extend(bytes(data))
        return len(data)

    def close(self):
        pass

    def status(self):
        return int(bytes(self.output).split(b" ", 2)[1])

    def body(self):
        return bytes(self.output).split(b"\r\n\r\n", 1)[1]

    def json(self):
        return json.loads(self.body().decode("utf-8"))


class Server:
    def __init__(self, conn, ip="192.168.1.77"):
        self.conn = conn
        self.ip = ip

    def accept(self):
        return self.conn, (self.ip, 50000)


def serve(request, ip="192.168.1.77"):
    conn = Conn(request)
    state.http_server_sock = Server(conn, ip)
    routes.handle_http_client()
    return conn


def browser_headers(kind, host, origin):
    """Header order as sent by each engine for a cross-site form POST."""
    if kind == "firefox":
        return [("Host", host), ("User-Agent", UA),
                ("Accept", "text/html,application/xhtml+xml,*/*;q=0.8"),
                ("Accept-Language", "en-US,en;q=0.5"),
                ("Accept-Encoding", "gzip, deflate"),
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("Content-Length", "0"), ("Origin", origin),
                ("Connection", "keep-alive"), ("Referer", origin + "/")]
    if kind == "chrome":
        return [("Host", host), ("Connection", "keep-alive"),
                ("Content-Length", "0"), ("Cache-Control", "max-age=0"),
                ("Origin", origin),
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("User-Agent", UA), ("Referer", origin + "/")]
    return [("Host", host),  # safari
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Origin", origin), ("Accept-Encoding", "gzip, deflate"),
            ("Connection", "keep-alive"), ("User-Agent", UA),
            ("Referer", origin + "/"), ("Content-Length", "0")]


def padded_cross_site_post(kind, host, target, cut_at):
    """Cross-site POST whose request line is padded so that exactly `cut_at`
    bytes precede the Origin header — the layout that used to push Origin
    past the bytes the server read."""
    headers = browser_headers(kind, host, "http://attacker.example")

    def render(path):
        head = ("POST %s HTTP/1.1\r\n" % path).encode()
        head += b"".join(("%s: %s\r\n" % h).encode() for h in headers)
        return head + b"\r\n"

    base = render(target + "&p=")
    need = cut_at - (base.find(b"\r\nOrigin:") + 2)
    assert need >= 0
    request = render(target + "&p=" + "A" * need)
    assert request.find(b"\r\nOrigin:") + 2 == cut_at
    return request


def same_origin_post(path, body, host="192.168.4.1", ctype=None, extra=()):
    ctype = ctype or "application/x-www-form-urlencoded"
    head = ("POST %s HTTP/1.1\r\nHost: %s\r\nOrigin: http://%s\r\n"
            "Content-Type: %s\r\nContent-Length: %d\r\n" %
            (path, host, host, ctype, len(body)))
    for key, value in extra:
        head += "%s: %s\r\n" % (key, value)
    return head.encode() + b"\r\n" + body


class Base(unittest.TestCase):
    def setUp(self):
        state.settings = {
            "device_id": "KC2-2026-0001-7",
            "sta_ssid": "HomeWiFi", "sta_password": "home-secret",
            "ap_ssid": "knowco2-AAAA", "ap_password": "ABCDEFGH",
            "admin_password": "", "cloud_api_url": "",
            "cloud_device_id": "", "cloud_device_token": "",
            "cloud_enabled": False, "altitude": 0,
        }
        self.mode(config.WIFI_MODE_AP)
        state.mdns_hostname = "knowco2-6677"
        state.sensor = None
        state._wd = None
        state.fs_readonly = False
        state.cloud_last_trace = ""
        state.pending_cloud_claim = None
        state.co2_history = []
        access.close_unlock_window()
        state.admin_fail_count = 0
        state.admin_lock_until = 0.0
        settings_mod.save_settings = lambda: True
        settings_mod.apply_settings = lambda: None
        for hook in ("show_busy", "clear_busy", "update_visibility",
                     "refresh_text", "show_status", "make_or_update_qrs",
                     "refresh_apinfo_screen", "apply_color_scheme"):
            setattr(routes.runtime, hook, lambda *a, **k: None)
        routes.runtime.compute_trend_arrow = lambda: "-"
        self._render = routes.portal_page.render_settings_page
        routes.portal_page.render_settings_page = lambda: "<html>SETTINGS</html>"
        helpers.LOG_ENABLED = False

    def tearDown(self):
        settings_mod.save_settings = _REAL_SAVE
        settings_mod.apply_settings = _REAL_APPLY
        routes.portal_page.render_settings_page = self._render
        access.close_unlock_window()
        helpers.LOG_ENABLED = True

    def mode(self, wifi_mode):
        state.wifi_mode = wifi_mode
        state.ip_str_cached = ("192.168.4.1" if wifi_mode == config.WIFI_MODE_AP
                               else "192.168.1.50")

    def set_admin(self, password):
        state.settings["admin_password"] = crypto.hash_admin_password(password)


# ----------------------------------------------------------------------
# 1. Cross-site request check must not be skippable by padding the URL
# ----------------------------------------------------------------------
class RequestSourceTests(Base):
    def test_truncated_head_is_never_an_allowed_source(self):
        allowed = ("192.168.4.1",)
        partial = b"POST / HTTP/1.1\r\nHost: 192.168.4.1\r\nUser-Agent: x\r\n"
        self.assertFalse(http_util.request_head_complete(partial))
        self.assertFalse(http_util.request_source_allowed(partial, allowed))
        self.assertTrue(http_util.request_source_allowed(partial + b"\r\n", allowed))

    def test_padded_cross_site_post_cannot_rewrite_settings(self):
        target = "/?sta_ssid=EvilTwin&sta_password=attacker-psk&admin_pw=attacker"
        for kind in ("chrome", "firefox", "safari"):
            # the old 2,048-byte window and the current limit
            for cut_at in (2048, config.HTTP_HEAD_MAX_BYTES):
                with self.subTest(browser=kind, cut_at=cut_at):
                    conn = serve(padded_cross_site_post(kind, "192.168.4.1", target, cut_at))
                    self.assertIn(conn.status(), (403, 431))
                    self.assertEqual(state.settings["sta_ssid"], "HomeWiFi")
                    self.assertEqual(state.settings["admin_password"], "")

    def test_padded_cross_site_post_cannot_force_calibration(self):
        calls = []
        state.sensor = types.SimpleNamespace(
            force_calibration=lambda ref: calls.append(ref) or True,
            set_asc=lambda v: None, set_altitude=lambda v: None,
            set_ambient_pressure=lambda v: None)
        for cut_at in (2048, config.HTTP_HEAD_MAX_BYTES):
            conn = serve(padded_cross_site_post(
                "firefox", "192.168.4.1", "/calibration?calibrate=1&ref=5000", cut_at))
            self.assertIn(conn.status(), (403, 431))
        self.assertEqual(calls, [])

    def test_padded_cross_site_post_cannot_trigger_url_install(self):
        self.mode(config.WIFI_MODE_STA)
        fetched = []
        original = routes.handle_update_route
        routes.handle_update_route = lambda conn, params, **kw: fetched.append(dict(params))
        try:
            access.open_unlock_window()
            for kind in ("chrome", "firefox", "safari"):
                for cut_at in (2048, config.HTTP_HEAD_MAX_BYTES):
                    conn = serve(padded_cross_site_post(
                        kind, "192.168.1.50",
                        "/update?firmware_url=http%3A%2F%2Fattacker.example%2Ffw.py",
                        cut_at))
                    self.assertIn(conn.status(), (403, 431))
        finally:
            routes.handle_update_route = original
        self.assertEqual(fetched, [])

    def test_oversized_head_is_rejected_with_431(self):
        request = (b"POST / HTTP/1.1\r\nHost: 192.168.4.1\r\nX-Pad: " +
                   b"a" * 5000 + b"\r\n\r\n")
        conn = serve(request)
        self.assertEqual(conn.status(), 431)

    def test_large_but_complete_same_origin_head_still_works(self):
        body = b"temp_mode=C&alerts=on"
        request = same_origin_post("/", body, extra=(("X-Pad", "a" * 2600),))
        self.assertGreater(request.find(b"\r\n\r\n"), 2048)
        conn = serve(request)
        self.assertEqual(conn.status(), 200)
        self.assertEqual(state.settings["temp_mode"], "C")

    def test_get_is_still_served_when_the_client_sends_a_partial_head(self):
        conn = serve(b"GET /api/v1/info HTTP/1.1\r\nHost: 192.168.4.1\r\n")
        self.assertEqual(conn.status(), 200)
        self.assertEqual(conn.json()["product"], "KnowCO2")

    def test_post_parameters_come_from_the_body_only(self):
        # Same-origin, complete head, but the settings ride in the URL.
        conn = serve(same_origin_post("/?sta_ssid=FromQuery&admin_pw=fromquery", b""))
        self.assertEqual(conn.status(), 200)
        self.assertEqual(state.settings["sta_ssid"], "HomeWiFi")
        self.assertEqual(state.settings["admin_password"], "")
        conn = serve(same_origin_post("/", b"sta_ssid=FromBody&alerts=on"))
        self.assertEqual(conn.status(), 200)
        self.assertEqual(state.settings["sta_ssid"], "FromBody")


# ----------------------------------------------------------------------
# 2 + 4. Firmware install: physical presence always, password never alone
# ----------------------------------------------------------------------
class OtaAuthorizationTests(Base):
    def test_password_alone_cannot_install(self):
        self.mode(config.WIFI_MODE_STA)
        self.set_admin("hunter2")
        self.assertFalse(routes.ota_authorized({"pw": "hunter2"}))

    def test_open_window_without_the_code_cannot_install(self):
        self.mode(config.WIFI_MODE_STA)
        code = access.open_unlock_window()
        self.assertFalse(routes.ota_authorized({}))
        wrong = "%06d" % ((int(code) + 1) % 1000000)
        self.assertFalse(routes.ota_authorized({"code": wrong}))
        self.assertTrue(routes.ota_authorized({"code": code}))
        self.assertTrue(routes.ota_authorized({"code": code[:3] + " " + code[3:]}))

    def test_password_is_required_in_addition_when_configured(self):
        self.mode(config.WIFI_MODE_STA)
        self.set_admin("hunter2")
        code = access.open_unlock_window()
        self.assertFalse(routes.ota_authorized({"code": code}))
        self.assertFalse(routes.ota_authorized({"code": code, "pw": "nope"}))
        self.assertTrue(routes.ota_authorized({"code": code, "pw": "hunter2"}))

    def test_wrong_codes_close_the_window(self):
        code = access.open_unlock_window()
        wrong = "%06d" % ((int(code) + 1) % 1000000)
        for _ in range(config.OTA_CODE_MAX_ATTEMPTS):
            self.assertFalse(routes.ota_authorized({"code": wrong}))
        self.assertFalse(access.unlock_active())
        self.assertFalse(routes.ota_authorized({"code": code}))

    def test_setup_ap_client_cannot_mint_ota_access(self):
        # Previously: set a password over the AP, then use it for OTA.
        conn = serve(same_origin_post("/", b"admin_pw=set-by-ap-client&alerts=on"))
        self.assertEqual(conn.status(), 200)
        self.assertTrue(crypto.is_hashed_admin_password(state.settings["admin_password"]))
        self.assertFalse(routes.ota_authorized({"pw": "set-by-ap-client"}))

    def test_unauthorized_install_never_touches_storage(self):
        touched = []
        originals = (routes.stream_request_body_to_file, routes._fs_junk_cleanup)
        routes.stream_request_body_to_file = lambda *a, **k: touched.append("stream") or (False, "x")
        routes._fs_junk_cleanup = lambda *a, **k: touched.append("cleanup") or 0
        try:
            self.mode(config.WIFI_MODE_STA)
            self.set_admin("hunter2")
            # locked
            conn = Conn()
            routes.handle_update_route(conn, {"upload": "1", "pw": "hunter2"}, method=b"POST")
            self.assertIn(b"OTA is locked", conn.output)
            # window open, code missing / wrong
            code = access.open_unlock_window()
            wrong = "%06d" % ((int(code) + 1) % 1000000)
            for params in ({"upload": "1", "pw": "hunter2"},
                           {"upload": "1", "pw": "hunter2", "code": wrong},
                           {"firmware_url": "https://example.com/code.py", "code": wrong}):
                conn = Conn()
                routes.handle_update_route(conn, dict(params), method=b"POST")
                self.assertIn(b"Unauthorized", conn.output)
        finally:
            routes.stream_request_body_to_file, routes._fs_junk_cleanup = originals
        self.assertEqual(touched, [])

    def test_uploader_credentials_travel_in_headers_not_the_url(self):
        seen = []
        original = routes.handle_update_route
        routes.handle_update_route = lambda conn, params, **kw: seen.append(dict(params))
        try:
            self.mode(config.WIFI_MODE_STA)
            self.set_admin("p@ss word+1")
            code = access.open_unlock_window()
            serve(same_origin_post(
                "/update?upload=1&pw=from-url&code=000000&firmware_url=http://x/y.py",
                b"PK\x03\x04", host="192.168.1.50", ctype="application/octet-stream",
                extra=(("X-KnowCO2-Code", code),
                       ("X-KnowCO2-Password", "p%40ss%20word%2B1"))))
        finally:
            routes.handle_update_route = original
        self.assertEqual(seen, [{"upload": "1", "code": code, "pw": "p@ss word+1"}])
        self.assertTrue(routes.ota_authorized(seen[0]))

    def test_precheck_reports_lock_and_code_state_without_touching_storage(self):
        self.mode(config.WIFI_MODE_STA)
        self.set_admin("hunter2")

        def check(body):
            return serve(same_origin_post("/update", body, host="192.168.1.50")).json()

        self.assertEqual(check(b"check=1&code=123456&pw=hunter2"),
                         {"ok": False, "unlocked": False})
        code = access.open_unlock_window()
        self.assertEqual(check(b"check=1&code=&pw=hunter2"), {"ok": False, "unlocked": True})
        self.assertEqual(check(("check=1&code=%s&pw=nope" % code).encode()),
                         {"ok": False, "unlocked": True})
        self.assertEqual(check(("check=1&code=%s&pw=hunter2" % code).encode()),
                         {"ok": True, "unlocked": True})

    def test_update_page_reflects_no_secret_and_keeps_them_out_of_urls(self):
        self.set_admin("hunter2")
        code = access.open_unlock_window()
        conn = Conn()
        routes.handle_update_route(conn, {"pw": "hunter2"}, method=b"GET")
        page = bytes(conn.output)
        self.assertFalse(b"hunter2" in page, "password reflected into the page")
        self.assertFalse(code.encode() in page, "unlock code rendered into the page")
        urls = re.findall(rb"\.open\('POST', '([^']*)'", page)
        self.assertEqual(sorted(urls), [b"/api/v1/factory-reset", b"/update", b"/update?upload=1"])
        self.assertTrue(b"X-KnowCO2-Code" in page)
        self.assertTrue(b"id='ota-pw'" in page)

    def test_locked_update_page_has_no_password_unlock(self):
        self.set_admin("hunter2")
        conn = Conn()
        routes.handle_update_route(conn, {}, method=b"GET")
        page = bytes(conn.output)
        self.assertTrue(b"locked" in page)
        self.assertFalse(b"type='password'" in page, "password-only unlock form is back")

    def test_url_install_requires_https(self):
        self.mode(config.WIFI_MODE_STA)
        code = access.open_unlock_window()
        conn = Conn()
        routes.handle_update_route(
            conn, {"firmware_url": "http://192.168.1.9/code.py", "code": code},
            method=b"POST")
        self.assertIn(b"must start with https://", conn.output)

    def test_url_install_is_size_capped(self):
        self.mode(config.WIFI_MODE_STA)
        code = access.open_unlock_window()

        class Response:
            status_code = 200

            def iter_content(self, chunk_size=512):
                for _ in range(config.OTA_MAX_BYTES // 512 + 8):
                    yield b"#" * 512

            def close(self):
                pass

        requests_stub = types.ModuleType("adafruit_requests")
        requests_stub.Session = lambda *a: types.SimpleNamespace(
            get=lambda url, timeout=None: Response())
        saved = (sys.modules.get("adafruit_requests"), routes.socketpool, routes.wifi)
        sys.modules["adafruit_requests"] = requests_stub
        routes.socketpool = types.SimpleNamespace(SocketPool=lambda radio: object())
        routes.wifi = wifi_mock
        with tempfile.TemporaryDirectory() as tmp:
            # Redirect the device-root paths the route writes into a sandbox.
            routes.open = lambda path, mode="r": builtins.open(
                os.path.join(tmp, path.lstrip("/")), mode)
            try:
                conn = Conn()
                routes.handle_update_route(
                    conn, {"firmware_url": "https://example.com/code.py", "code": code},
                    method=b"POST")
                written = os.path.getsize(os.path.join(tmp, "code.py.ota"))
            finally:
                del routes.open
                routes.socketpool, routes.wifi = saved[1], saved[2]
                if saved[0] is None:
                    sys.modules.pop("adafruit_requests", None)
                else:
                    sys.modules["adafruit_requests"] = saved[0]
        self.assertIn(b"Download rejected: larger than", conn.output)
        self.assertLessEqual(written, config.OTA_MAX_BYTES)


# ----------------------------------------------------------------------
# 2 + 6. Admin password: hashed at rest, rate limited
# ----------------------------------------------------------------------
class AdminPasswordTests(Base):
    def test_password_is_stored_as_a_salted_hash(self):
        serve(same_origin_post("/", b"admin_pw=correct+horse&alerts=on"))
        stored = state.settings["admin_password"]
        self.assertTrue(stored.startswith(crypto.ADMIN_HASH_PREFIX))
        self.assertNotIn("correct horse", stored)
        self.assertNotEqual(stored, crypto.hash_admin_password("correct horse"))  # salted
        self.assertTrue(access.check_admin_password("correct horse"))
        self.assertFalse(access.check_admin_password("correct horsf"))

    def test_legacy_plaintext_password_still_verifies(self):
        state.settings["admin_password"] = "old-plain"
        self.assertTrue(access.check_admin_password("old-plain"))
        self.assertFalse(access.check_admin_password("kc2h1$"))

    def test_plaintext_password_is_migrated_on_load(self):
        settings_mod.save_settings = _REAL_SAVE
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with open("settings.json", "w") as handle:
                    json.dump({"admin_password": "old-plain", "ap_ssid": "knowco2-AAAA",
                               "ap_password": "ABCDEFGH"}, handle)
                state.settings = {}
                settings_mod.load_settings()
                with open("settings.json") as handle:
                    on_disk = json.load(handle)
            finally:
                os.chdir(cwd)
        self.assertTrue(crypto.is_hashed_admin_password(on_disk["admin_password"]))
        self.assertNotIn("old-plain", json.dumps(on_disk))
        self.assertTrue(access.check_admin_password("old-plain"))

    def test_repeated_wrong_passwords_lock_and_presence_clears(self):
        self.set_admin("hunter2")
        for index in range(config.ADMIN_LOCKOUT_AFTER):
            self.assertFalse(access.check_admin_password("guess%d" % index))
        self.assertGreater(access.admin_lock_remaining(), 0)
        self.assertFalse(access.check_admin_password("hunter2"))  # locked out
        access.open_unlock_window()                               # A + B on the device
        self.assertEqual(access.admin_lock_remaining(), 0)
        self.assertTrue(access.check_admin_password("hunter2"))

    def test_lockout_is_bounded_and_missing_password_is_not_a_guess(self):
        self.set_admin("hunter2")
        for _ in range(50):
            access.check_admin_password("")
            access.check_admin_password(None)
        self.assertEqual(state.admin_fail_count, 0)
        for index in range(40):
            state.admin_lock_until = 0.0
            access.check_admin_password("guess%d" % index)
        self.assertLessEqual(state.admin_lock_until - time.monotonic(),
                             config.ADMIN_LOCKOUT_MAX_S + 1)

    def test_login_page_gates_settings_and_reports_the_lock(self):
        self.set_admin("hunter2")
        conn = serve(same_origin_post("/", b"pw=wrong"))
        self.assertIn(b"Please enter the settings password", conn.output)
        self.assertNotIn(b"SETTINGS", conn.output)
        conn = serve(same_origin_post("/", b"pw=hunter2"))
        self.assertIn(b"SETTINGS", conn.output)
        for index in range(config.ADMIN_LOCKOUT_AFTER):
            serve(same_origin_post("/", ("pw=guess%d" % index).encode()))
        conn = serve(same_origin_post("/", b"pw=hunter2"))
        self.assertIn(b"Too many wrong passwords", conn.output)


# ----------------------------------------------------------------------
# 3 + 8. Setup-AP credentials
# ----------------------------------------------------------------------
class SetupApCredentialTests(Base):
    def test_alphabet_has_no_duplicates_or_lookalikes(self):
        self.assertEqual(len(set(helpers.SAFE31)), len(helpers.SAFE31))
        self.assertEqual(len(helpers.SAFE31), 31)
        for char in "01OIL":
            self.assertNotIn(char, helpers.SAFE31)

    def test_generated_tokens_are_uniform_over_the_alphabet(self):
        sample = "".join(helpers.rand_safe32(8) for _ in range(4000))
        self.assertTrue(set(sample) <= set(helpers.SAFE31))
        expected = len(sample) / 31.0
        for char in helpers.SAFE31:
            self.assertLess(abs(sample.count(char) - expected), expected * 0.25, char)
        self.assertEqual(len(helpers.rand_safe32(12)), 12)

    def test_generated_ap_password_uses_the_configured_length(self):
        ssid, password = settings_mod.generate_ap_credentials()
        self.assertTrue(ssid.startswith("knowco2-"))
        self.assertEqual(len(password), config.AP_PASSWORD_LENGTH)
        self.assertGreaterEqual(config.AP_PASSWORD_LENGTH, 8)

    def test_published_ap_password_is_replaced_at_boot(self):
        self.assertTrue(settings_mod._BURNED_AP_PASSWORD_SHA256)
        for digest in settings_mod._BURNED_AP_PASSWORD_SHA256:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        original = settings_mod._BURNED_AP_PASSWORD_SHA256
        import hashlib
        settings_mod._BURNED_AP_PASSWORD_SHA256 = original + (
            hashlib.sha256(b"PUBLISHD").hexdigest(),)
        try:
            state.settings["ap_ssid"] = "knowco2-B778"
            state.settings["ap_password"] = "PUBLISHD"
            settings_mod.ensure_ap_credentials()
            self.assertNotEqual(state.settings["ap_password"], "PUBLISHD")
            self.assertNotEqual(state.settings["ap_ssid"], "knowco2-B778")
            kept = state.settings["ap_password"]
            settings_mod.ensure_ap_credentials()  # a good password is left alone
            self.assertEqual(state.settings["ap_password"], kept)
        finally:
            settings_mod._BURNED_AP_PASSWORD_SHA256 = original

    def test_long_passwords_do_not_run_into_the_qr_code(self):
        with open(os.path.join(REPO_ROOT, "knowco2", "ui", "screens.py"), encoding="utf-8") as handle:
            screens = handle.read()
        self.assertIn("state.wifi_mode == config.WIFI_MODE_AP and len(pw) <= 8", screens)


# ----------------------------------------------------------------------
# 3. settings.json is device state, never source or release content
# ----------------------------------------------------------------------
class RuntimeStateHygieneTests(unittest.TestCase):
    def test_settings_files_are_ignored_and_untracked(self):
        with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as handle:
            ignored = handle.read().split()
        for name in ("settings.json", "settings.json.bak"):
            self.assertIn(name, ignored)
        if not os.path.isdir(os.path.join(REPO_ROOT, ".git")):
            self.skipTest("not a git checkout")
        try:
            tracked = subprocess.run(
                ["git", "-C", REPO_ROOT, "ls-files", "--", "settings.json",
                 "settings.json.bak", "settings.json.tmp", "settings.json.new"],
                capture_output=True, text=True, check=True, timeout=30).stdout.split()
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git unavailable")
        self.assertEqual(tracked, [])

    def test_release_tree_validator_rejects_runtime_state(self):
        tool = os.path.join(REPO_ROOT, "tools", "validate_release_tree.py")
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "code.py"), "w").close()
            clean = subprocess.run([sys.executable, tool, tmp], capture_output=True)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            with open(os.path.join(tmp, "settings.json.bak"), "w") as handle:
                handle.write('{"ap_password":"must-not-ship"}')
            dirty = subprocess.run([sys.executable, tool, tmp], capture_output=True)
            self.assertNotEqual(dirty.returncode, 0)
            self.assertIn(b"runtime state", dirty.stderr)


# ----------------------------------------------------------------------
# 6. Factory reset: physical presence only
# ----------------------------------------------------------------------
class FactoryResetTests(Base):
    def _post(self, payload):
        return serve(same_origin_post(
            "/api/v1/factory-reset", json.dumps(payload).encode(),
            ctype="application/json"))

    def test_requires_the_window_and_the_on_screen_code(self):
        called = []
        settings_mod_reset = settings_mod.factory_reset
        settings_mod.factory_reset = lambda: called.append(1) or True
        try:
            conn = self._post({"code": "123456", "confirm": "ERASE"})
            self.assertEqual(conn.status(), 403)
            code = access.open_unlock_window()
            wrong = "%06d" % ((int(code) + 1) % 1000000)
            self.assertEqual(self._post({"code": wrong, "confirm": "ERASE"}).status(), 403)
            self.assertEqual(self._post({"code": code}).status(), 422)
            self.assertEqual(self._post({"code": code, "confirm": "yes"}).status(), 422)
            self.assertEqual(called, [])
        finally:
            settings_mod.factory_reset = settings_mod_reset

    def test_erases_every_secret_without_needing_the_password(self):
        settings_mod.save_settings = _REAL_SAVE
        sleeps = []
        real_sleep = routes.time.sleep
        routes.time.sleep = lambda seconds: sleeps.append(seconds)
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                self.set_admin("forgotten")
                state.settings["cloud_device_token"] = "c2VjcmV0"
                state.settings["mqtt_pass"] = "broker-secret"
                self.assertTrue(settings_mod.save_settings())
                self.assertTrue(os.path.exists("settings.json.bak"))
                code = access.open_unlock_window()
                conn = self._post({"code": code, "confirm": "ERASE"})
                left = sorted(os.listdir(tmp))
            finally:
                os.chdir(cwd)
                routes.time.sleep = real_sleep
        self.assertEqual(conn.status(), 200)
        self.assertTrue(conn.json()["factory_reset"])
        self.assertEqual(left, [])
        self.assertEqual(state.settings["admin_password"], "")
        self.assertEqual(state.settings["sta_password"], "")
        self.assertEqual(state.settings["cloud_device_token"], "")
        self.assertEqual(state.settings["mqtt_pass"], "")
        self.assertEqual(state.settings["ap_password"], "")  # regenerated on next boot
        self.assertFalse(access.unlock_active())
        self.assertEqual(sleeps, [1.5])

    def test_cross_site_request_is_refused(self):
        code = access.open_unlock_window()
        body = json.dumps({"code": code, "confirm": "ERASE"}).encode()
        request = (b"POST /api/v1/factory-reset HTTP/1.1\r\nHost: 192.168.4.1\r\n"
                   b"Origin: http://attacker.example\r\nContent-Type: application/json\r\n"
                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        self.assertEqual(serve(request).status(), 403)


# ----------------------------------------------------------------------
# 7. Cloud destination and secret cannot be set through the settings form
# ----------------------------------------------------------------------
class CloudDestinationTests(Base):
    def test_legacy_form_ignores_cloud_destination_and_secret(self):
        body = (b"cloud_api_url=http%3A%2F%2Fattacker.example&cloud_token=QUFBQQ%3D%3D"
                b"&cloud_device_token=QkJCQg%3D%3D&cloud_device_secret=Q0NDQw%3D%3D&alerts=on")
        conn = serve(same_origin_post("/", body))
        self.assertEqual(conn.status(), 200)
        self.assertEqual(state.settings["cloud_api_url"], "")
        self.assertEqual(state.settings["cloud_device_token"], "")

    def test_cloud_upload_refuses_cleartext_http(self):
        saved = (cloud.adafruit_requests, cloud.ssl, cloud.socketpool, cloud.wifi,
                 cloud._get_session)
        cloud.adafruit_requests = cloud.ssl = cloud.socketpool = cloud.wifi = object()

        def no_session():
            raise AssertionError("a session must not be opened for an http:// URL")

        cloud._get_session = no_session
        try:
            state.cloud_enabled = True
            state.cloud_device_token = "U1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1M="
            state.cloud_api_url = "http://api.knowco2.com"
            self.mode(config.WIFI_MODE_STA)
            self.assertFalse(cloud.cloud_send({"co2": 600}))
            self.assertIn("https", state.cloud_last_error)
        finally:
            (cloud.adafruit_requests, cloud.ssl, cloud.socketpool, cloud.wifi,
             cloud._get_session) = saved
            state.cloud_enabled = False
            state.cloud_device_token = ""
            state.cloud_api_url = ""


# ----------------------------------------------------------------------
# 9. /status must not hand internal tracebacks to the whole network
# ----------------------------------------------------------------------
class StatusDisclosureTests(Base):
    def test_traceback_only_while_physically_unlocked(self):
        self.mode(config.WIFI_MODE_STA)
        state.cloud_last_trace = 'File "knowco2/telemetry/cloud.py", line 492'
        conn = serve(b"GET /status HTTP/1.1\r\nHost: 192.168.1.50\r\n\r\n")
        payload = conn.json()
        self.assertNotIn("cloud_last_trace", payload)
        self.assertIn("co2", payload)                  # Home Assistant fields stay
        self.assertIn("firmware_version", payload)
        access.open_unlock_window()
        conn = serve(b"GET /status HTTP/1.1\r\nHost: 192.168.1.50\r\n\r\n")
        self.assertIn("cloud_last_trace", conn.json())


# ----------------------------------------------------------------------
# 11. NTP answers are bounded
# ----------------------------------------------------------------------
class NtpTests(Base):
    def setUp(self):
        super().setUp()
        self.mode(config.WIFI_MODE_STA)
        self.rtc_writes = []
        outer = self

        class RTC:
            @property
            def datetime(self):
                return None

            @datetime.setter
            def datetime(self, value):
                outer.rtc_writes.append(value)

        self._saved = (ntp.rtc.__dict__.get("RTC"), ntp._query_once, ntp.wifi,
                       state.ntp_synced, state.last_ntp_sync)
        ntp.rtc.RTC = RTC
        ntp.wifi = wifi_mock
        ntp.runtime.show_status = lambda *a, **k: None

    def tearDown(self):
        if self._saved[0] is None:
            ntp.rtc.__dict__.pop("RTC", None)
        else:
            ntp.rtc.RTC = self._saved[0]
        ntp._query_once, ntp.wifi = self._saved[1], self._saved[2]
        state.ntp_synced, state.last_ntp_sync = self._saved[3], self._saved[4]
        super().tearDown()

    def _answers(self, values):
        queue = list(values)
        asked = []

        def query(host, timeout=1.5):
            asked.append(host)
            return queue.pop(0) if queue else None

        ntp._query_once = query
        return asked

    def test_floor_is_the_firmware_build_date(self):
        self.assertGreaterEqual(config.NTP_MIN_VALID_UNIX, 1793491200)  # 2026-11-01

        class Pool:
            AF_INET = SOCK_DGRAM = 0

            def __init__(self, seconds):
                self.seconds = seconds

            def getaddrinfo(self, host, port):
                return [(0, 0, 0, "", ("192.0.2.1", port))]

            def socket(self, family, kind):
                pool = self

                class Socket:
                    def settimeout(self, value):
                        pass

                    def sendto(self, data, address):
                        pass

                    def recv_into(self, buffer, size):
                        stamp = pool.seconds + config.NTP_UNIX_DELTA
                        buffer[40:44] = stamp.to_bytes(4, "big")
                        return 48

                    def close(self):
                        pass

                return Socket()

        saved = (ntp.socketpool, state.socket_pool)
        ntp.socketpool = object()
        try:
            state.socket_pool = Pool(1600000000)  # 2020: plausible to the old check
            self.assertIsNone(ntp._query_once("time.example"))
            state.socket_pool = Pool(config.NTP_MIN_VALID_UNIX + 86400)
            self.assertEqual(ntp._query_once("time.example"),
                             config.NTP_MIN_VALID_UNIX + 86400)
        finally:
            ntp.socketpool, state.socket_pool = saved

    def test_first_sync_accepts_one_server(self):
        state.ntp_synced = False
        now = int(time.time())
        asked = self._answers([now])
        self.assertTrue(ntp.ntp_sync(force=True))
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(self.rtc_writes), 1)

    def test_large_step_needs_a_second_server_to_agree(self):
        state.ntp_synced = True
        now = int(time.time())
        far = now + 5 * 365 * 86400
        self._answers([far, now])            # second server disagrees
        self.assertFalse(ntp.ntp_sync(force=True))
        self.assertEqual(self.rtc_writes, [])
        self._answers([far, far + 2])        # two servers agree: genuine correction
        self.assertTrue(ntp.ntp_sync(force=True))
        self.assertEqual(len(self.rtc_writes), 1)

    def test_small_drift_is_applied_from_one_server(self):
        state.ntp_synced = True
        asked = self._answers([int(time.time()) + 3])
        self.assertTrue(ntp.ntp_sync(force=True))
        self.assertEqual(len(asked), 1)


# ----------------------------------------------------------------------
# Physical unlock plumbing
# ----------------------------------------------------------------------
class UnlockWindowTests(Base):
    def test_code_is_fresh_numeric_and_never_in_any_response(self):
        codes = {access.open_unlock_window() for _ in range(50)}
        self.assertGreater(len(codes), 40)
        for code in codes:
            self.assertRegex(code, r"^\d{%d}$" % config.OTA_CODE_DIGITS)
        code = access.open_unlock_window()
        self.mode(config.WIFI_MODE_STA)
        for path in ("/status", "/api/v1/info", "/api/v1/settings", "/update", "/data"):
            conn = serve(("GET %s HTTP/1.1\r\nHost: 192.168.1.50\r\n\r\n" % path).encode())
            self.assertNotIn(code.encode(), conn.output, path)

    def test_decimal_codes_are_unbiased_digits(self):
        sample = "".join(helpers.rand_digits(6) for _ in range(5000))
        expected = len(sample) / 10.0
        for digit in "0123456789":
            self.assertLess(abs(sample.count(digit) - expected), expected * 0.15, digit)

    def test_main_loop_shows_the_code_and_keeps_it_on_screen(self):
        with open(os.path.join(REPO_ROOT, "code.py"), encoding="utf-8") as handle:
            loop = handle.read()
        self.assertIn("access.open_unlock_window()", loop)
        self.assertIn("access.service_code_display(W.status_label, now)", loop)
        self.assertNotIn("state.ota_unlock_until = now", loop)

    def test_code_stays_on_the_status_line_until_the_window_closes(self):
        label = types.SimpleNamespace(text="")
        state.status_timeout = 0.0
        code = access.open_unlock_window()
        now = time.monotonic()
        access.service_code_display(label, now)
        self.assertEqual(label.text, "OTA code " + code)
        # another status message takes the line for its own timeout...
        label.text = "Time sync: OK"
        state.status_timeout = now + 3.0
        access.service_code_display(label, now + 1.0)
        self.assertEqual(label.text, "Time sync: OK")
        # ...and the code returns once the main loop has expired it
        label.text = ""
        state.status_timeout = 0.0
        access.service_code_display(label, now + 3.1)
        self.assertEqual(label.text, "OTA code " + code)
        # window closed by wrong codes (or factory reset): line is cleared
        access.close_unlock_window()
        access.service_code_display(label, now + 4.0)
        self.assertEqual(label.text, "")
        # window expiring by time closes it and clears the line
        code = access.open_unlock_window()
        access.service_code_display(label, time.monotonic())
        self.assertEqual(label.text, "OTA code " + code)
        access.service_code_display(label, state.ota_unlock_until + 0.1)
        self.assertEqual(label.text, "")
        self.assertEqual(state.ota_unlock_code, "")

    def test_closing_the_window_never_wipes_someone_elses_status(self):
        label = types.SimpleNamespace(text="")
        state.status_timeout = 0.0
        access.open_unlock_window()
        now = time.monotonic()
        access.service_code_display(label, now)
        label.text = "Calibrated to 420 ppm"
        state.status_timeout = now + 3.0
        access.close_unlock_window()
        access.service_code_display(label, now + 0.5)
        self.assertEqual(label.text, "Calibrated to 420 ppm")
        state.status_timeout = 0.0

    def test_usb_api_never_reveals_the_code(self):
        sys.modules.setdefault("usb_cdc", types.ModuleType("usb_cdc"))
        from knowco2 import usb_api
        code = access.open_unlock_window()
        for method in ("hello", "status.get", "settings.get", "unlock.status"):
            result, error = usb_api._dispatch(method, {})
            self.assertIsNone(error)
            self.assertNotIn(code, json.dumps(result), method)


# ----------------------------------------------------------------------
# 5. Build pipeline
# ----------------------------------------------------------------------
class WorkflowTests(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(REPO_ROOT, ".github", "workflows", name), encoding="utf-8") as handle:
            return handle.read()

    def test_every_action_is_pinned_to_a_commit(self):
        for name in ("ci.yml", "release.yml", "dependency-monitor.yml"):
            uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", self._read(name), re.M)
            self.assertTrue(uses, name)
            for ref in uses:
                self.assertRegex(ref, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$", name)

    def test_release_runs_every_suite_that_ci_runs(self):
        def suites(text):
            return set(re.findall(r"python3 (?:-m unittest )?(test_\w+\.py)", text))

        ci, release = suites(self._read("ci.yml")), suites(self._read("release.yml"))
        self.assertIn("test_security_hardening.py", ci)
        self.assertEqual(ci - release, set())

    def test_workflow_inputs_are_not_interpolated_into_shell(self):
        release = self._read("release.yml")
        for line in release.splitlines():
            if "github.event.inputs" in line:
                self.assertRegex(line, r"^\s+[A-Z_]+: \$\{\{ github\.event\.inputs\.\w+ \}\}$")
        self.assertIn("SHA256SUMS", release)


if __name__ == "__main__":
    unittest.main()
