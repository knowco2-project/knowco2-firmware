# knowco2/web/routes.py
# ----------------------------------------------------------------------
# HTTP route handlers, the tiny raw-socket server loop, and the OTA update
# flow (bounded signed ZIP packages only).
#
# Routes: /data /status /export.csv /calibration /update  and  / (settings).
#
# UI side effects (status messages, screen refresh, QR redraw, trend arrow)
# go through `runtime` hooks so this module never imports the UI layer.
# Sensor calibration goes through the CO2Sensor driver abstraction, so a new
# sensor needs no changes here.
# ----------------------------------------------------------------------

import time
import gc
import json

from .. import state, config, version, runtime
from .. import crypto
from .. import settings as settings_mod
from ..net import wifi as wifi_mod
from ..helpers import log, as_int, clamp_int, rand_token
from . import portal_page
from .api_v1_contract import (
    API_SCHEMA_VERSION,
    info_payload,
    normalize_claim_request,
    normalize_settings_patch,
    settings_payload,
    wifi_write_payload,
)
from .http_util import (
    send_all, build_response, make_json_response, make_html_response,
    parse_query, url_decode, read_request_head, read_request_body,
    stream_request_body_to_file, request_source_allowed, CAPTIVE_PATHS_204,
)

try:
    import wifi
    import socketpool
except ImportError:
    wifi = None
    socketpool = None

try:
    import microcontroller
except Exception:
    microcontroller = None

SETTINGS_FILE = config.SETTINGS_FILE


def _send_json(conn, payload, status=200):
    header, body = make_json_response(payload, status=status, cors=False)
    send_all(conn, header)
    send_all(conn, body)


def _header_value(request_raw, name):
    """Return one request header without retaining or logging the body."""
    wanted = name.lower().encode("ascii") + b":"
    headers = request_raw.split(b"\r\n\r\n", 1)[0].replace(b"\r\n", b"\n")
    for line in headers.split(b"\n")[1:]:
        if line.lower().startswith(wanted):
            try:
                return line.split(b":", 1)[1].strip().decode("ascii", "ignore")
            except Exception:
                return ""
    return ""


def _request_content_length(request_raw):
    try:
        return int(_header_value(request_raw, "content-length") or 0)
    except Exception:
        return -1


def _allowed_management_hosts():
    """Return the exact local names that may address a mutating request."""
    hosts = ["192.168.4.1"]
    if state.ip_str_cached:
        hosts.append(state.ip_str_cached)
    if state.mdns_hostname:
        hosts.append(state.mdns_hostname)
        hosts.append(state.mdns_hostname + ".local")
    return hosts


def _api_error(conn, status, code, message):
    _send_json(conn, {
        "schema_version": API_SCHEMA_VERSION,
        "ok": False,
        "error": {"code": code, "message": message},
    }, status=status)


def _device_serial():
    for key in ("serial", "serial_number", "device_serial"):
        value = str(state.settings.get(key) or "").strip()
        if value:
            return value[:64]
    value = str(state.settings.get("device_id") or "").strip()
    if value.upper().startswith("KC2-"):
        return value[:64]
    return None


def _wifi_is_configured():
    return any(
        (state.settings.get("sta_ssid" + suffix) or "").strip()
        and (state.settings.get("sta_password" + suffix) or "").strip()
        for suffix in ("", "_2", "_3")
    )


def _activation_state():
    if state.pending_cloud_claim:
        return "pending"
    if state.cloud_device_token:
        return "configured"
    return state.cloud_activation_state or "unconfigured"


def _api_write_authorized(payload):
    """AP possession authorizes setup; STA writes require admin password.

    The admin password is removed before validation and is never reflected.
    Fresh devices expose their random WPA-protected AP specifically for local
    onboarding. Once on a shared LAN, an explicit configured admin credential
    is required, preventing unauthenticated drive-by writes.
    """
    provided = payload.pop("admin_password", "") if isinstance(payload, dict) else ""
    if state.wifi_mode == config.WIFI_MODE_AP:
        return True
    return check_admin_password(provided) and ota_unlock_active()


def _save_or_restore(changes):
    """Apply a small settings mutation atomically from the caller's view."""
    old = {}
    for key, value in changes.items():
        old[key] = state.settings.get(key)
        state.settings[key] = value
    if settings_mod.save_settings():
        settings_mod.apply_settings()
        return True
    for key, value in old.items():
        state.settings[key] = value
    settings_mod.apply_settings()
    return False


# ── Auth helpers ─────────────────────────────────────────────────────
def check_admin_password(provided):
    """Constant-time check of `provided` against the configured admin
    password. Returns False when no admin password is set."""
    admin_pw = state.settings.get("admin_password", "") or ""
    if not admin_pw or not isinstance(provided, str) or len(provided) > 64:
        return False
    return crypto.constant_time_equals(provided, admin_pw)


def ota_unlock_active():
    """True while the physical-presence OTA window (A+B hold) is open."""
    try:
        return bool(state.ota_unlock_until) and \
            time.monotonic() < state.ota_unlock_until
    except Exception:
        return False


def ota_authorized(params):
    """Match provisioning: setup AP possession, or admin AND physical STA unlock."""
    if state.wifi_mode == config.WIFI_MODE_AP:
        return True
    return check_admin_password(params.get("pw", "")) and ota_unlock_active()


# ----------------------------------------------------------------------
# Forced calibration (goes through the sensor driver)
# ----------------------------------------------------------------------
def perform_force_calibration(ref_ppm):
    """Force-calibrate the active sensor against a known reference ppm.
    Records the calibration in settings on success. Returns True/False."""
    if state.sensor is None:
        runtime.show_status("Calibration failed")
        return False
    try:
        target = int(ref_ppm)
        if target < 300 or target > 10000:
            return False
    except Exception:
        return False
    if not state.sensor.force_calibration(target):
        log("calibration", "force_calibration failed", min_interval=2.0)
        runtime.show_status("Calibration failed")
        return False
    try:
        ts = time.time()
    except Exception:
        ts = time.monotonic()
    state.settings["last_calibration_ts"] = ts
    state.settings["last_calibration_ref"] = target
    settings_mod.save_settings()
    runtime.show_status("Calibrated to %d ppm" % target)
    return True


# ----------------------------------------------------------------------
# Simple data routes
# ----------------------------------------------------------------------
def handle_data_route(conn):
    data_points = state.co2_history[-config.MAX_WEB_POINTS:]
    ints = []
    for v in data_points:
        if isinstance(v, (int, float)):
            iv = as_int(v)
            if iv is not None:
                ints.append(iv)
    header, body = make_json_response({"co2": ints}, cors=True)
    send_all(conn, header)
    send_all(conn, body)


# ----------------------------------------------------------------------
# Versioned local setup API (browser, iOS, Android, desktop)
# ----------------------------------------------------------------------
def handle_api_info_route(conn):
    mdns_name = None
    if state.wifi_mode == config.WIFI_MODE_STA and state.mdns_hostname:
        mdns_name = state.mdns_hostname + ".local"
    payload = info_payload(
        device_id=state.settings.get("device_id"),
        cloud_device_id=state.cloud_device_id or None,
        serial=_device_serial(),
        board_id=state.board_id_str,
        firmware_version=version.FIRMWARE_VERSION,
        cp_version=version.CP_VERSION,
        wifi_mode=state.wifi_mode,
        wifi_configured=_wifi_is_configured(),
        mdns=mdns_name,
        cloud_configured=bool(state.cloud_device_token),
        cloud_enabled=state.cloud_enabled,
        activation_state=_activation_state(),
    )
    _send_json(conn, payload)


def handle_api_settings_route(conn, method, payload):
    if method in (b"GET", b"HEAD"):
        _send_json(conn, settings_payload(
            state.settings,
            state.settings.get("_settings_version", 0),
        ))
        return
    if method != b"PATCH":
        _api_error(conn, 405, "method_not_allowed", "Use GET or PATCH")
        return
    if not _api_write_authorized(payload):
        _api_error(conn, 403, "physical_setup_required", "Use setup AP mode or unlock the device")
        return
    try:
        patch = normalize_settings_patch(payload)
    except (ValueError, TypeError) as exc:
        _api_error(conn, 422, "invalid_settings", str(exc))
        return
    low = int(patch.get("low_threshold", state.LOW_THRESHOLD))
    med = int(patch.get("med_threshold", state.MED_THRESHOLD))
    alert = int(patch.get("alert_threshold", state.ALERT_THRESHOLD))
    if not (low < med < alert):
        _api_error(
            conn, 422, "invalid_settings",
            "Thresholds must remain ordered low < medium < alert",
        )
        return
    next_version = int(state.settings.get("_settings_version", 0) or 0) + 1
    patch["_settings_version"] = next_version
    if not _save_or_restore(patch):
        _api_error(conn, 503, "storage_unavailable", "Settings could not be saved")
        return
    runtime.update_visibility()
    runtime.refresh_text()
    _send_json(conn, settings_payload(state.settings, next_version))


def _schedule_sta_connect(requested):
    if not requested:
        return False
    state._sta_fallback = True
    state._sta_auto_retry_count = 0
    state.last_sta_auto_retry = 0.0
    state.onboarding_connect_after = (
        time.monotonic() + config.ONBOARDING_CONNECT_DELAY_S
    )
    return True


def _requested_connect(payload, default=True):
    value = payload.get("connect", default)
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError("connect must be a boolean")


def _validate_wifi_api(payload):
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    allowed = ("ssid", "password", "connect")
    for key in payload:
        if key not in allowed:
            raise ValueError("unsupported Wi-Fi field: %s" % key)
    return wifi_write_payload(payload.get("ssid"), payload.get("password"))


def handle_api_wifi_route(conn, method, payload):
    if method != b"POST":
        _api_error(conn, 405, "method_not_allowed", "Use POST")
        return
    if not _api_write_authorized(payload):
        _api_error(conn, 403, "physical_setup_required", "Use setup AP mode or unlock the device")
        return
    try:
        wifi_values = _validate_wifi_api(payload)
        connect = _requested_connect(payload)
    except (ValueError, TypeError) as exc:
        _api_error(conn, 422, "invalid_wifi", str(exc))
        return
    if not _save_or_restore({
        "sta_ssid": wifi_values["ssid"],
        "sta_password": wifi_values["password"],
    }):
        _api_error(conn, 503, "storage_unavailable", "Wi-Fi credentials could not be saved")
        return
    scheduled = _schedule_sta_connect(connect)
    _send_json(conn, {
        "schema_version": API_SCHEMA_VERSION,
        "ok": True,
        "wifi": {"configured": True},
        "connection_scheduled": scheduled,
    }, status=202 if scheduled else 200)


def _validate_cloud_claim_api(payload):
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    allowed = ("claim_code", "activation_token", "connect")
    for key in payload:
        if key not in allowed:
            raise ValueError("unsupported cloud claim field: %s" % key)
    return normalize_claim_request(payload)


def handle_api_cloud_claim_route(conn, method, payload):
    if method != b"POST":
        _api_error(conn, 405, "method_not_allowed", "Use POST")
        return
    if not _api_write_authorized(payload):
        _api_error(conn, 403, "physical_setup_required", "Use setup AP mode or unlock the device")
        return
    if state.cloud_device_token:
        _api_error(conn, 409, "already_paired", "Forget local cloud credentials before pairing again")
        return
    try:
        claim = _validate_cloud_claim_api(payload)
        connect = _requested_connect(payload)
    except (ValueError, TypeError) as exc:
        _api_error(conn, 422, "invalid_claim", str(exc))
        return
    state.pending_cloud_claim = claim
    state.cloud_activation_request_id = rand_token(32)
    state.cloud_activation_state = "pending"
    state.cloud_activation_error = ""
    state.cloud_activation_failures = 0
    state.cloud_activation_next_attempt = 0.0
    scheduled = _schedule_sta_connect(connect)
    _send_json(conn, {
        "schema_version": API_SCHEMA_VERSION,
        "ok": True,
        "cloud": {"activation_state": "pending"},
        "connection_scheduled": scheduled,
    }, status=202)


def handle_api_onboarding_route(conn, method, payload):
    """Atomically queue Wi-Fi plus optional cloud activation for the wizard."""
    if method != b"POST":
        _api_error(conn, 405, "method_not_allowed", "Use POST")
        return
    if not _api_write_authorized(payload):
        _api_error(conn, 403, "physical_setup_required", "Use setup AP mode or unlock the device")
        return
    if not isinstance(payload, dict):
        _api_error(conn, 422, "invalid_onboarding", "Request must be an object")
        return
    for key in payload:
        if key not in ("wifi", "cloud", "connect"):
            _api_error(conn, 422, "invalid_onboarding", "Unsupported onboarding field")
            return
    try:
        wifi_values = _validate_wifi_api(dict(payload.get("wifi") or {}))
        cloud_input = payload.get("cloud")
        claim = normalize_claim_request(cloud_input) if cloud_input else None
        connect = _requested_connect(payload)
    except (ValueError, TypeError) as exc:
        _api_error(conn, 422, "invalid_onboarding", str(exc))
        return
    if claim and state.cloud_device_token:
        _api_error(conn, 409, "already_paired", "Forget local cloud credentials before pairing again")
        return
    changes = {
        "sta_ssid": wifi_values["ssid"],
        "sta_password": wifi_values["password"],
    }
    if not _save_or_restore(changes):
        _api_error(conn, 503, "storage_unavailable", "Onboarding settings could not be saved")
        return
    if claim:
        state.pending_cloud_claim = claim
        state.cloud_activation_request_id = rand_token(32)
        state.cloud_activation_state = "pending"
        state.cloud_activation_error = ""
        state.cloud_activation_failures = 0
        state.cloud_activation_next_attempt = 0.0
    scheduled = _schedule_sta_connect(connect)
    _send_json(conn, {
        "schema_version": API_SCHEMA_VERSION,
        "ok": True,
        "wifi": {"configured": True},
        "cloud": {"activation_state": "pending" if claim else _activation_state()},
        "connection_scheduled": scheduled,
    }, status=202 if scheduled else 200)


def handle_api_cloud_clear_route(conn, method, payload, forget=False):
    if method != b"POST":
        _api_error(conn, 405, "method_not_allowed", "Use POST")
        return
    if not _api_write_authorized(payload):
        _api_error(conn, 403, "physical_setup_required", "Use setup AP mode or unlock the device")
        return
    changes = {}
    if forget:
        changes["cloud_device_id"] = ""
        changes["cloud_device_token"] = ""
        changes["cloud_enabled"] = False
    if changes and not _save_or_restore(changes):
        _api_error(conn, 503, "storage_unavailable", "Cloud pairing state could not be cleared")
        return
    state.pending_cloud_claim = None
    state.cloud_activation_request_id = None
    state.cloud_activation_state = "unconfigured" if forget else (
        "configured" if state.cloud_device_token else "unconfigured"
    )
    state.cloud_activation_error = ""
    state.cloud_activation_failures = 0
    state.cloud_activation_next_attempt = 0.0
    _send_json(conn, {
        "schema_version": API_SCHEMA_VERSION,
        "ok": True,
        "cloud": {"activation_state": _activation_state()},
    })


def handle_api_cloud_enabled_route(conn, method, payload):
    if method != b"POST":
        _api_error(conn, 405, "method_not_allowed", "Use POST")
        return
    if not _api_write_authorized(payload):
        _api_error(conn, 403, "physical_setup_required", "Use setup AP mode or unlock the device")
        return
    if tuple(payload.keys()) != ("enabled",):
        _api_error(conn, 422, "invalid_request", "Provide only enabled")
        return
    value = payload.get("enabled")
    if not isinstance(value, bool):
        _api_error(conn, 422, "invalid_request", "enabled must be a boolean")
        return
    if value and not state.cloud_device_token:
        _api_error(conn, 409, "not_paired", "Complete cloud pairing before enabling uploads")
        return
    if not _save_or_restore({"cloud_enabled": value}):
        _api_error(conn, 503, "storage_unavailable", "Cloud setting could not be saved")
        return
    _send_json(conn, {
        "schema_version": API_SCHEMA_VERSION,
        "ok": True,
        "cloud": {"enabled": state.cloud_enabled},
    })


def handle_export_csv_route(conn):
    """Return the in-RAM CO2 history as a downloadable CSV file."""
    try:
        import rtc as _rtc  # noqa: F401
        now_ts = int(time.time())  # noqa: F841
    except Exception:
        now_ts = 0  # noqa: F841

    rows = ["seconds_ago,co2_ppm,temp_c,rh_pct"]
    pts = state.co2_history[-config.MAX_WEB_POINTS:]
    total = len(pts)
    for i, v in enumerate(pts):
        age_s = int((total - 1 - i) * config.SCD_MEASUREMENT_PERIOD)
        co2_val = int(v) if v is not None else ""
        rows.append("%d,%s,," % (age_s, co2_val))
    if total > 0 and state.last_temp_c is not None and state.last_rh is not None:
        rows[-1] = "0,%s,%.1f,%.1f" % (
            int(pts[-1]) if pts[-1] is not None else "",
            state.last_temp_c,
            state.last_rh,
        )
    csv_body = "\r\n".join(rows) + "\r\n"
    csv_bytes = csv_body.encode("utf-8")
    header = build_response(200, "text/csv; charset=utf-8", csv_bytes)[0]
    header = header.replace(
        b"\r\n\r\n",
        b"\r\nContent-Disposition: attachment; filename=\"knowco2_export.csv\"\r\n\r\n",
        1,
    )
    send_all(conn, header)
    send_all(conn, csv_bytes)


def handle_status_route(conn):
    s = state.settings
    if state.last_temp_c is not None:
        t_c = state.last_temp_c
        t_f = t_c * 9.0 / 5.0 + 32.0
        temp_display = t_f if state.temp_mode == "F" else t_c
    else:
        temp_display = None

    arrow = runtime.compute_trend_arrow()
    from .. import battery
    vbat, pct = battery.read()

    payload = {
        "device_id": s.get("device_id", "co2-node-1"),
        "co2": state.last_co2,
        "temp_c": state.last_temp_c,
        "rh": state.last_rh,
        "temp_mode": state.temp_mode,
        "temp_display": temp_display,
        "trend_arrow": arrow,

        "display_mode": state.display_mode,
        "alerts_enabled": state.alerts_enabled,
        "low_threshold": state.LOW_THRESHOLD,
        "med_threshold": state.MED_THRESHOLD,
        "alert_threshold": state.ALERT_THRESHOLD,
        "history_points": len(state.co2_history),

        "board_id": state.board_id_str,
        "firmware_version": version.FIRMWARE_VERSION,
        "cp_version": version.CP_VERSION,

        "battery_v": vbat,
        "battery_pct": pct,
        "battery_gauge": state.fuel_gauge_kind,
        "battery_bus": state.fuel_bus_name,

        "wifi_mode": state.wifi_mode,
        "fs_readonly": state.fs_readonly,
        "safe_mode_recoveries": state.safe_mode_recoveries,
        "ip": state.ip_str_cached,
        "mdns": (state.mdns_hostname + ".local") if (state.wifi_mode == config.WIFI_MODE_STA and state.mdns_hostname) else None,

        "cloud_enabled": state.cloud_enabled,
        "cloud_device_id": state.cloud_device_id or None,
        "cloud_activation_state": _activation_state(),
        "cloud_interval_sec": state.cloud_interval_sec,
        "cloud_configured": bool(state.cloud_api_url) and bool(state.cloud_device_token),
        "cloud_last_attempt_ts": state.cloud_last_attempt_ts,
        "cloud_last_http": state.cloud_last_http,
        "cloud_last_error": state.cloud_last_error,
        "cloud_failures": state.cloud_failures,
        "cloud_consec_memerr": state.cloud_consec_memerr,
        "rate_of_change": state.rate_of_change,
    }

    # ESP-IDF internal heap (what TLS/mbedTLS allocates from) — sampled
    # live so /status is useful even before the first cloud attempt.
    try:
        from ..telemetry import cloud as _cloud_mod
        _idf_free, _idf_big = _cloud_mod.idf_heap_info()
        payload["idf_free"] = _idf_free
        payload["idf_largest_block"] = _idf_big
    except Exception:
        pass

    # Failure forensics (RC-48v3): what exactly failed, and what the heaps
    # looked like at that instant.
    if state.cloud_last_trace:
        payload["cloud_last_trace"] = state.cloud_last_trace
        payload["idf_free_at_fail"] = state.idf_free_at_fail
        payload["idf_largest_at_fail"] = state.idf_largest_at_fail
        payload["mem_free_at_fail"] = state.mem_free_at_fail

    payload["energy_mode"] = state.energy_mode
    payload["scd_period_effective"] = state._scd_period_effective

    try:
        payload["uptime_s"] = int(time.monotonic() - state.boot_time_mono)
    except Exception:
        pass
    try:
        payload["mem_free"] = gc.mem_free()
        payload["mem_alloc"] = gc.mem_alloc()
        payload["mem_free_min"] = state.mem_free_min if state.mem_samples else None
        payload["mem_free_max"] = state.mem_free_max if state.mem_samples else None
        payload["mem_free_ema"] = int(state.mem_free_ema) if state.mem_samples else None
        payload["mem_samples"] = state.mem_samples
        payload["last_gc_s_ago"] = int(time.monotonic() - state.last_gc_ts) if state.last_gc_ts else None
    except Exception:
        pass

    try:
        _age = time.monotonic() - state.last_scd_sample_ts
        payload["last_sensor_sample_s"] = int(_age)
        payload["sensor_ok"] = (_age <= config.SCD_SAMPLE_TIMEOUT)
    except Exception:
        pass

    try:
        payload["asc_enabled"] = bool(s.get("asc_enabled", True))
        payload["altitude"] = int(s.get("altitude", 0) or 0)
        payload["ambient_pressure"] = int(s.get("ambient_pressure", 0) or 0)
        payload["last_calibration_ts"] = s.get("last_calibration_ts", 0)
        payload["last_calibration_ref"] = s.get("last_calibration_ref", 0)
    except Exception:
        pass

    # Status contains operational diagnostics and stable identifiers. Keep it
    # same-origin so an arbitrary page cannot inventory a reachable device.
    header, body = make_json_response(payload, cors=False)
    send_all(conn, header)
    send_all(conn, body)


# ----------------------------------------------------------------------
# Calibration page + route
# ----------------------------------------------------------------------
def render_calibration_page():
    s = state.settings
    asc_enabled = bool(s.get("asc_enabled", True))
    asc_checked = "checked" if asc_enabled else ""
    altitude = s.get("altitude", 0)
    pressure = s.get("ambient_pressure", 0)
    last_ts = s.get("last_calibration_ts", 0)
    last_ref = s.get("last_calibration_ref", 0)
    if last_ts:
        try:
            lt = time.localtime(last_ts)
            last_ts_str = "%04d-%02d-%02d %02d:%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min)
        except Exception:
            last_ts_str = str(last_ts)
    else:
        last_ts_str = "never"
    calibration_text = ("%d ppm" % int(last_ref)) if last_ref else "none"

    def esc(v):
        try:
            return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        except Exception:
            return ""

    ALTITUDE_MAX = config.ALTITUDE_MAX
    PRESSURE_MAX = config.PRESSURE_MAX

    calibration_pw_field = ""
    if s.get("admin_password", ""):
        calibration_pw_field = (
            "<label>Confirm settings password<br>"
            "<input type='password' name='pw' autocomplete='current-password' value=''></label>"
        )
    html = """<!DOCTYPE html>
<html>
<head>
  <meta charset='utf-8'>
  <title>Calibration</title>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background:#0b0b0b; color:#eee; margin:0; }
    .wrap { max-width:480px; margin:0 auto; padding:16px; }
    h1 { font-size:20px; margin:0 0 10px 0; }
    label { display:block; margin-top:12px; font-size:13px; }
    input, select { width:100%; max-width:260px; padding:6px; border-radius:4px; border:1px solid #444; background:#111; color:#eee; font-size:13px; }
    input[type=checkbox] { width:auto; max-width:none; }
    button { margin-top:12px; padding:8px 16px; border-radius:4px; border:1px solid #00bcd4; background:#00bcd4; color:#000; font-weight:600; cursor:pointer; }
    button:hover { background:#26c6da; border-color:#26c6da; }
    small { color:#aaa; font-size:11px; }
    .muted { color:#aaa; }
  </style>
</head>
<body>
  <div class='wrap'>
    <h1>Calibration</h1>
    <form method='POST' action='/calibration'>
      """ + calibration_pw_field + """<fieldset style='border:1px solid #333; border-radius:8px; padding:10px;'>
        <legend style='font-size:12px; color:#aaa;'>Calibration Settings</legend>
        <label>Altitude (m)
          <input type='number' name='altitude' min='0' max='""" + str(ALTITUDE_MAX) + """' value='""" + esc(altitude) + """'>
        </label>
        <label>Ambient pressure (hPa)
          <input type='number' name='pressure' min='0' max='""" + str(PRESSURE_MAX) + """' value='""" + esc(pressure) + """'>
        </label>
        <label>
          <input type='checkbox' name='asc' """ + asc_checked + """> Enable Automatic Self Calibration (ASC)
        </label>
        <label>Force calibration reference (ppm)
          <input type='number' name='ref' placeholder='e.g. 420'>
        </label>
        <div style='margin-top:12px;'>
          <button type='submit' name='update' value='1'>Update Settings</button>
          <button type='submit' name='calibrate' value='1'>Calibrate Now</button>
          <button type='submit' name='reset' value='1'>Revert Defaults</button>
        </div>
      </fieldset>
    </form>
    <p style='margin-top:16px;'>Last calibration: <strong>""" + calibration_text + """</strong><br>
       Time: <strong>""" + last_ts_str + """</strong></p>
    <p><a href='/' style='color:#00bcd4;'>Back to settings</a></p>
  </div>
</body>
</html>"""
    return html


def handle_calibration_route(conn, params, method=b"GET"):
    """Apply altitude/pressure/ASC/forced-calibration changes (via the sensor
    driver) and render the calibration page. Write ops are admin-password
    protected when one is configured; the read view is always available."""
    s = state.settings
    sensor = state.sensor
    scd_available = (sensor is not None)

    _WRITE_PARAMS = {"reset", "calibrate", "altitude", "pressure", "asc", "update"}
    _has_write_op = method == b"POST" and params and any(k in params for k in _WRITE_PARAMS)
    if _has_write_op and state.wifi_mode != config.WIFI_MODE_AP:
        if not (check_admin_password(params.get("pw", "")) and ota_unlock_active()):
            _api_error(
                conn, 403, "physical_setup_required",
                "Switch to setup AP, or provide the admin password after physically unlocking the device",
            )
            return

    if _has_write_op:
        if "reset" in params:
            s["asc_enabled"] = True
            s["altitude"] = 0
            s["ambient_pressure"] = 0
            s["last_calibration_ts"] = 0
            s["last_calibration_ref"] = 0
            if scd_available:
                sensor.set_asc(True)
                sensor.set_altitude(0)
                sensor.set_ambient_pressure(0)
            else:
                runtime.show_status("Sensor unavailable")
            settings_mod.save_settings()
            runtime.show_status("Calibration reset to defaults")
        else:
            if "asc" not in params and s.get("asc_enabled", True):
                params["asc"] = "on"
            if "altitude" in params and params["altitude"]:
                alt_val = as_int(params["altitude"])
                if alt_val is not None:
                    if alt_val != 0:
                        alt_val = clamp_int(alt_val, config.ALTITUDE_MIN, config.ALTITUDE_MAX, alt_val)
                    s["altitude"] = alt_val
            if "pressure" in params and params["pressure"]:
                p_val = as_int(params["pressure"])
                if p_val is not None:
                    if p_val != 0:
                        p_val = clamp_int(p_val, config.PRESSURE_MIN_NONZERO, config.PRESSURE_MAX, p_val)
                    s["ambient_pressure"] = p_val
            s["asc_enabled"] = ("asc" in params)
            if "calibrate" in params:
                try:
                    ref_val = int(params.get("ref", "0"))
                except Exception:
                    ref_val = None
                if ref_val:
                    if scd_available:
                        perform_force_calibration(ref_val)
                    else:
                        runtime.show_status("Sensor unavailable")
            # Apply calibration to the sensor immediately (via the driver).
            if scd_available:
                sensor.set_asc(bool(s.get("asc_enabled", True)))
                sensor.set_altitude(s.get("altitude", 0))
                sensor.set_ambient_pressure(s.get("ambient_pressure", 0))
            else:
                runtime.show_status("Sensor unavailable")
            settings_mod.save_settings()
            runtime.show_status("Calibration settings updated")
    html = render_calibration_page()
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)


# ----------------------------------------------------------------------
# ZIP package OTA helpers
# ----------------------------------------------------------------------
def _zip_ensure_dir(path):
    parts = path.split("/")
    absolute = path.startswith("/")
    current = ""
    for part in parts[:-1]:
        if not part:
            continue
        if current:
            current = current + "/" + part
        else:
            current = ("/" + part) if absolute else part
        try:
            import os as _e_os
            _e_os.mkdir(current)
        except OSError:
            pass  # already exists


def _fs_free_bytes():
    """Free bytes on the device filesystem, or None if unknown."""
    try:
        import os as _s_os
        st = _s_os.statvfs("/")
        return st[1] * st[4]  # f_frsize * f_bavail
    except Exception:
        return None


def _fs_junk_cleanup(root="/"):
    """Reclaim flash before an OTA. Removes things that are never firmware:
    macOS Finder metadata sprayed onto the FAT volume during USB rescues
    (.DS_Store, ._AppleDouble files, .Trashes, .fseventsd, .Spotlight-V100),
    plus stale artifacts of interrupted updates (/update.zip, /update.tmp,
    /code.py.ota, /ota_staged). Returns bytes freed. Never raises."""
    import os as _c_os
    freed = [0]

    def _rm_file(path):
        try:
            freed[0] += _c_os.stat(path)[6]
        except Exception:
            pass
        try:
            _c_os.remove(path)
        except Exception:
            pass

    def _rm_tree(path):
        try:
            for name in _c_os.listdir(path):
                child = path + "/" + name
                try:
                    if _c_os.stat(child)[0] & 0x4000:
                        _rm_tree(child)
                    else:
                        _rm_file(child)
                except Exception:
                    pass
            _c_os.rmdir(path)
        except Exception:
            pass

    def _walk(path):
        try:
            names = _c_os.listdir(path)
        except Exception:
            return
        for name in names:
            child = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
            if name == ".DS_Store" or name.startswith("._"):
                _rm_file(child)
                continue
            try:
                if _c_os.stat(child)[0] & 0x4000:
                    if name in (".Trashes", ".fseventsd", ".Spotlight-V100",
                                ".TemporaryItems", "ota_staged"):
                        _rm_tree(child)
                    else:
                        _walk(child)
            except Exception:
                pass

    _walk(root)
    for stale in ("/update.zip", "/update.tmp", "/code.py.ota"):
        try:
            _c_os.stat(stale)
            _rm_file(stale)
        except Exception:
            pass
    return freed[0]


def _wd_feed():
    """Feed the hardware watchdog; safe no-op when absent."""
    try:
        if state._wd is not None:
            state._wd.feed()
    except Exception:
        pass


# Empty on-device; tests set this to a temporary sandbox, never the host root.
_OTA_FS_ROOT = ""


def _ota_path(path):
    return _OTA_FS_ROOT + "/" + path.lstrip("/")


def _process_zip_update(conn, zip_path):
    """Verify every signed byte first, then stage/commit with a durable floor.

    Per-file rename is not an atomic whole-image update. Interrupted commits
    may require the physical service path; only exact pending manifests retry.
    """
    import os as _oz
    import gc as _gcz
    from .. import ota_security

    try:
        if state._wd is not None:
            state._wd.timeout = 90
    except Exception:
        pass
    try:
        plan = ota_security.verify_package(zip_path, watchdog=_wd_feed)
    except Exception as error:
        try: _oz.remove(zip_path)
        except Exception: pass
        _send_ota_result(conn, False, "Signed OTA rejected: " + str(error) +
                         ". No live firmware files were changed.")
        return
    safe = [(e["name"], e) for e in plan["entries"]]
    safe.sort(key=lambda item: (item[0] == "code.py", item[0]))
    stage_root = _ota_path("ota_staged")

    def rmtree(path):
        try:
            for name in _oz.listdir(path):
                child = path + "/" + name
                if _oz.stat(child)[0] & 0x4000:
                    rmtree(child)
                else:
                    _oz.remove(child)
            _oz.rmdir(path)
        except OSError:
            pass

    def abort(message):
        rmtree(stage_root)
        try: _oz.remove(zip_path)
        except Exception: pass
        _send_ota_result(conn, False, message + ". No live firmware files were changed.")

    def extract(entry, target):
        _zip_ensure_dir(target)
        ota_security.extract_verified_entry(zip_path, entry, target, watchdog=_wd_feed)
        _gcz.collect()

    def commit(name, staged):
        target = _ota_path(name)
        if name == "code.py":
            try: _oz.remove(_ota_path("code.py.bak"))
            except OSError: pass
            try: _oz.rename(target, _ota_path("code.py.bak"))
            except OSError: pass
        else:
            try: _oz.remove(target)
            except OSError: pass
        _zip_ensure_dir(target)
        _oz.rename(staged, target)
        # A stale source/bytecode counterpart can shadow the verified update.
        # Entry points deliberately stay source; remove module aliases only.
        counterpart = None
        if name.startswith(("knowco2/", "lib/")):
            if name.endswith(".mpy"):
                counterpart = target[:-4] + ".py"
            elif name.endswith(".py"):
                counterpart = target[:-3] + ".mpy"
        if counterpart is not None:
            try: _oz.remove(counterpart)
            except OSError:
                try: _oz.stat(counterpart)
                except OSError: pass
                else: raise ValueError("Could not remove stale module alias")

    rmtree(stage_root)
    free = _fs_free_bytes()
    total = sum(e["uncomp_size"] for _, e in safe)
    largest = max(e["uncomp_size"] for _, e in safe)
    per_file = free is not None and free < total + 49152
    if per_file and free < largest + 32768:
        abort("Not enough flash space even for file-by-file installation")
        return
    staged = []
    if not per_file:
        try:
            for name, entry in safe:
                target = stage_root + "/" + name
                extract(entry, target)
                staged.append((name, target))
        except Exception as error:
            abort("Staging failed: " + str(error))
            return
    # Never replace an executable before the antirollback reservation is durable.
    try:
        ota_security.reserve_version(plan)
    except Exception as error:
        abort("Version reservation failed: " + str(error))
        return
    installed = 0
    try:
        if per_file:
            for name, entry in safe:
                target = stage_root + "/stagefile.tmp"
                extract(entry, target)
                commit(name, target)
                installed += 1
        else:
            for name, target in staged:
                _wd_feed()
                commit(name, target)
                installed += 1
        ota_security.complete_version(plan)
    except Exception as error:
        rmtree(stage_root)
        _send_ota_result(conn, False,
            "OTA interrupted after %d/%d files: %s. Retry this exact signed OTA; "
            "physical service recovery may be required." % (installed, len(safe), error))
        return
    rmtree(stage_root)
    try: _oz.remove(zip_path)
    except Exception: pass
    label = "TEST ONLY public-key fixture. " if plan["test_only"] else ""
    mode = " (low-space per-file mode)" if per_file else ""
    _send_ota_result(conn, True, label + "Installed and SHA-256 verified %d files%s. "
                     "Rebooting in 3 seconds." % (installed, mode))
    time.sleep(3)
    try:
        if microcontroller is not None:
            microcontroller.reset()
    except Exception:
        pass
    try:
        import supervisor as _sup
        _sup.reload()
    except Exception:
        pass


def handle_update_route(conn, params, method=b"GET", raw_headers=b""):
    """Signed ZIP upload only; never install arbitrary Python or fetched URLs.

    A setup AP is the local onboarding boundary. On shared STA networks both
    the admin credential and the live physical A+B unlock are required.
    Upload credentials travel in a header, never a URL or rendered response.
    """
    if method not in (b"GET", b"HEAD", b"POST"):
        _api_error(conn, 405, "method_not_allowed", "Use GET or POST")
        return
    if method == b"POST":
        if params.get("upload") != "1" or "firmware_url" in params:
            _api_error(conn, 400, "signed_upload_required", "Upload a signed OTA ZIP; URL and Python updates are disabled")
            return
        if not ota_authorized(params):
            _api_error(conn, 403, "physical_setup_required",
                       "Use the setup AP, or provide the admin password after holding A+B for 3 seconds")
            return

    # --- FILE UPLOAD: POST with ?upload=1, raw binary body ---
    if method == b"POST" and params.get("upload") == "1":
        content_length = _request_content_length(raw_headers)
        if content_length <= 0 or content_length > 512 * 1024:
            _api_error(conn, 413, "invalid_upload_size", "A bounded nonempty signed ZIP is required")
            return
        # RC-48v4: reclaim junk (macOS metadata, stale OTA artifacts) and
        # refuse the upload EARLY if it cannot fit, instead of dying with
        # ENOSPC halfway through the stream.
        _freed = _fs_junk_cleanup()
        if _freed:
            log("ota", "pre-upload cleanup freed %d bytes" % _freed)
        _need = content_length
        _free = _fs_free_bytes()
        if _need is not None and _free is not None and _need + 16384 > _free:
            _send_ota_result(conn, False,
                "Not enough flash space for the upload: need %d bytes plus "
                "working room, %d free (junk cleanup already reclaimed %d). "
                "Use service-volume recovery or a smaller supported package; "
                "keep your known-good recovery backup." % (_need, _free, _freed))
            return

        tmp_path = "/update.tmp"
        ok, msg = stream_request_body_to_file(conn, raw_headers, tmp_path, max_bytes=512 * 1024)
        if not ok:
            _send_ota_result(conn, False, "Upload failed: " + msg)
            try:
                import os as _os2
                _os2.remove(tmp_path)
            except Exception:
                pass
            return

        try:
            with open(tmp_path, "rb") as _f:
                _magic = _f.read(4)
        except Exception as _me:
            _send_ota_result(conn, False, "Cannot read uploaded file: " + str(_me))
            return

        if _magic == b"PK\x03\x04":
            import os as _oz_mv
            zip_path = "/update.zip"
            try:
                _oz_mv.rename(tmp_path, zip_path)
            except Exception:
                zip_path = tmp_path
            _process_zip_update(conn, zip_path)
            return

        try:
            import os as _os
            _os.remove(tmp_path)
        except Exception:
            pass
        _send_ota_result(conn, False, "Only signed OTA ZIP packages are accepted. Nothing was installed.")
        return

    form_html = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>KnowCO2 - Signed OTA Update</title>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<style>
body{font-family:sans-serif;background:#0b0b0b;color:#eee;margin:0}
.wrap{max-width:640px;margin:auto;padding:16px}h1,a{color:#00bcd4}
label{display:block;margin:14px 0}input,button{font:inherit;padding:10px;max-width:100%;box-sizing:border-box}
input{background:#151515;color:#eee;border:1px solid #555;border-radius:4px}
button{background:#b71c1c;color:white;border:0;border-radius:4px;cursor:pointer}
button:disabled{opacity:.5}p{line-height:1.5}.warn{color:#ffca28}
</style></head><body><div class='wrap'>
<h1>Signed OTA Update</h1>
<p class='warn'>Security test firmware: use a dedicated test device. Do not disconnect power during installation.</p>
<p>Upload a ZIP signed by the verification key provisioned on this device. Unsigned ZIPs,
Python files and URL downloads are not accepted.</p>
<p>Connect to the device setup AP, or on your Wi-Fi network enter the admin password
and hold physical buttons <strong>A + B</strong> for 3 seconds to open the 5-minute update window.</p>
<p class='warn'>The local portal uses HTTP, not encrypted HTTPS. Prefer the isolated setup AP;
do not send your admin password over an untrusted shared network.</p>
<label>Admin password (required on Wi-Fi client mode)<br>
<input type='password' id='ota-password' maxlength='64' autocomplete='current-password' value=''></label>
<label>Signed OTA package<br><input type='file' id='fw-file-input' accept='.zip'></label>
<button id='upload-btn' onclick='doUpload()'>Upload &amp; Install</button>
<p id='upload-progress' role='status'></p>
<p>Settings are preserved. Keep a local service-volume backup and the recovery image available.</p>
<p><a href='/'>Back to Settings</a></p>
</div><script>
function doUpload(){
  var file = document.getElementById('fw-file-input').files[0];
  var button = document.getElementById('upload-btn');
  var progress = document.getElementById('upload-progress');
  if (!file || !/\\.zip$/i.test(file.name)) { progress.textContent = 'Choose a signed ZIP package.'; return; }
  if (!confirm('Install ' + file.name + '? The device will reboot.')) return;
  button.disabled = true;
  progress.textContent = 'Uploading...';
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/update?upload=1', true);
  xhr.setRequestHeader('Content-Type', 'application/octet-stream');
  xhr.setRequestHeader('X-KnowCO2-Admin-Password', document.getElementById('ota-password').value);
  document.getElementById('ota-password').value = '';
  xhr.onload = function(){
    button.disabled = false;
    if ((xhr.getResponseHeader('Content-Type') || '').indexOf('application/json') >= 0) {
      try { progress.textContent = JSON.parse(xhr.responseText).error.message; }
      catch (e) { progress.textContent = 'Update request was rejected.'; }
    } else { document.open(); document.write(xhr.responseText); document.close(); }
  };
  xhr.onerror = function(){ progress.textContent = 'Connection interrupted. Check the device before retrying.'; button.disabled = false; };
  xhr.upload.onprogress = function(e){ if (e.lengthComputable) progress.textContent = 'Uploading... ' + Math.round(e.loaded/e.total*100) + '%'; };
  xhr.upload.onload = function(){ progress.textContent = 'Verifying and installing; do not disconnect power.'; };
  xhr.send(file);
}
</script></body></html>"""
    header, body = make_html_response(form_html)
    send_all(conn, header)
    send_all(conn, body)


def _send_ota_result(conn, success, message):
    # Escape: the message can echo bytes from an uploaded file.
    try:
        message = (str(message).replace("&", "&amp;")
                   .replace("<", "&lt;").replace(">", "&gt;"))
    except Exception:
        message = "OTA result"
    color = "#4caf50" if success else "#e53935"
    icon = "&#10003;" if success else "&#10007;"
    html = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>OTA Result</title>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<style>body{font-family:sans-serif;background:#0b0b0b;color:#eee;text-align:center;padding:32px}
h2{color:%s}</style></head><body>
<h2>%s %s</h2><p>%s</p>
<a href='/' style='color:#00bcd4'>Back to Settings</a>
</body></html>""" % (color, icon, "Success" if success else "Error", message)
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)


def handle_root_route(conn, params, method=b"GET"):
    """Settings page (and POST handler). Admin-password gated when configured."""
    s = state.settings
    try:
        admin_pw = s.get("admin_password", "")
    except Exception:
        admin_pw = ""
    if admin_pw:
        provided_pw = params.get("pw")
        if not provided_pw or not crypto.constant_time_equals(provided_pw, admin_pw):
            login_html = """<!DOCTYPE html>
<html>
<head>
  <meta charset='utf-8'>
  <title>KnowCO2 Settings Login</title>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background:#0b0b0b; color:#eee; margin:0; }
    .wrap { max-width:480px; margin:0 auto; padding:16px; text-align:center; }
    label { display:block; margin-top:16px; font-size:14px; }
    input { padding:6px; border-radius:4px; border:1px solid #444; background:#111; color:#eee; font-size:14px; width:80%; max-width:260px; }
    button { margin-top:16px; padding:8px 16px; border-radius:4px; border:1px solid #00bcd4; background:#00bcd4; color:#000; font-weight:600; cursor:pointer; }
    button:hover { background:#26c6da; border-color:#26c6da; }
    small { color:#aaa; font-size:12px; }
  </style>
</head>
<body>
  <div class='wrap'>
    <h1>Know CO&#8322;</h1>
    <p>Please enter the settings password to continue.</p>
    <form method='POST' action='/'>
      <label>Password<br><input type='password' name='pw' autocomplete='off'></label>
      <button type='submit'>Unlock</button>
    </form>
    <small>If you forget the password, you may need to reset the device to clear it.</small>
  </div>
</body>
</html>"""
            header, body = make_html_response(login_html)
            send_all(conn, header)
            send_all(conn, body)
            return

    settings_params = None
    if params:
        settings_params = {}
        for k, v in params.items():
            if k != "pw":
                settings_params[k] = v
    if settings_params and len(settings_params) > 0 and method == b"POST":
        # Legacy form writes follow the same security boundary as Local API
        # v1. AP possession is the setup proof. On a shared STA network both
        # the configured admin password and live physical unlock are required.
        if state.wifi_mode != config.WIFI_MODE_AP:
            if not (check_admin_password(params.get("pw", "")) and ota_unlock_active()):
                _api_error(
                    conn, 403, "physical_setup_required",
                    "Switch to setup AP, or provide the admin password after physically unlocking the device",
                )
                return
        if "cloud_enabled" not in settings_params and s.get("cloud_enabled", False):
            settings_params["cloud_enabled"] = "on"
        if "alerts" not in settings_params and s.get("alerts_enabled", False):
            settings_params["alerts"] = "on"

        ap_changed = settings_mod.update_settings_from_params(settings_params)
        runtime.update_visibility()
        runtime.refresh_text()

        if ap_changed and state.wifi_mode == config.WIFI_MODE_AP:
            wifi_mod.switch_to_ap(force_restart=True)

        if state.screen == config.SCREEN_APINFO:
            if state.wifi_mode == config.WIFI_MODE_AP:
                runtime.make_or_update_qrs(s.get("ap_ssid", ""), s.get("ap_password", ""), state.ip_str_cached or "192.168.4.1")
            runtime.refresh_apinfo_screen()

        runtime.show_status("AP regenerated" if "regen_ap" in settings_params else "Web settings updated")

    html = portal_page.render_settings_page()
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)


# ----------------------------------------------------------------------
# HTTP server
# ----------------------------------------------------------------------
def start_http_server():
    """Start (or restart) the HTTP server in AP or STA mode. Returns True/False."""
    if wifi is None or socketpool is None:
        print("HTTP server: wifi/socketpool unavailable")
        return False

    try:
        if state.http_server_sock is not None:
            try:
                state.http_server_sock.close()
            except Exception:
                pass
            state.http_server_sock = None
    except Exception:
        pass

    try:
        state.socket_pool = socketpool.SocketPool(wifi.radio)
        srv = state.socket_pool.socket(state.socket_pool.AF_INET, state.socket_pool.SOCK_STREAM)

        try:
            srv.setsockopt(state.socket_pool.SOL_SOCKET, state.socket_pool.SO_REUSEADDR, 1)
        except Exception:
            pass

        bind_ip = "0.0.0.0"
        try:
            ap_ip = wifi.radio.ipv4_address_ap
            if ap_ip:
                bind_ip = str(ap_ip)
        except Exception:
            pass

        try:
            srv.bind((bind_ip, 80))
        except Exception as e:
            if bind_ip != "0.0.0.0":
                try:
                    srv.bind(("0.0.0.0", 80))
                except Exception as e2:
                    print("HTTP server bind failed:", e2)
                    return False
            else:
                print("HTTP server bind failed:", e)
                return False

        srv.listen(4)
        try:
            srv.settimeout(0)
        except Exception:
            pass

        state.http_server_sock = srv
        print("HTTP server listening on %s:80" % bind_ip)
        return True

    except Exception as e:
        print("HTTP server start error:", e)
        try:
            state.http_server_sock = None
        except Exception:
            pass
        return False


def handle_http_client():
    if state.http_server_sock is None:
        return

    try:
        conn, addr = state.http_server_sock.accept()
    except OSError:
        return

    runtime.show_busy("web")  # RC-52: serving a page can block ~0.1-2.5 s
    try:
        state.last_http_ts = time.monotonic()  # someone is using the web UI
        data = read_request_head(conn)
        if not data:
            try: conn.close()
            except Exception: pass
            return

        first_line = data.replace(b"\r\n", b"\n").split(b"\n", 1)[0]

        parts = first_line.split()
        method = b"GET"
        path = "/"
        if len(parts) >= 1:
            method = parts[0]
        if len(parts) >= 2:
            try:
                path = parts[1].decode("utf-8", "ignore")
            except Exception:
                path = "/"

        if path.startswith("http://"):
            try:
                path = path.split("://", 1)[1]
                path = path[path.find("/"):] if "/" in path else "/"
            except Exception:
                path = "/"

        # Never log query strings or request bodies: legacy URLs can contain
        # admin passwords and onboarding credentials are bearer secrets.
        log_path = path.split("?", 1)[0]
        log("req", "HTTP", addr, method, log_path, min_interval=0.2)

        if path in CAPTIVE_PATHS_204:
            if state.wifi_mode == config.WIFI_MODE_AP:
                header = (b"HTTP/1.1 302 Found\r\n"
                          b"Location: http://192.168.4.1/\r\n"
                          b"Content-Length: 0\r\n"
                          b"Connection: close\r\n\r\n")
                send_all(conn, header)
            else:
                header, body = build_response(204, "text/plain; charset=utf-8", b"")
                send_all(conn, header)
            return
        if path in ("/favicon.ico",):
            header, body = build_response(204, "image/x-icon", b"")
            send_all(conn, header)
            return

        if method not in (b"GET", b"HEAD", b"POST", b"PATCH"):
            header, body = build_response(405, "text/plain; charset=utf-8", b"Method Not Allowed")
            send_all(conn, header)
            send_all(conn, body)
            return

        if not request_source_allowed(data, _allowed_management_hosts()):
            _api_error(
                conn, 403, "invalid_request_source",
                "Host or browser request source is not allowed",
            )
            return

        route, params = parse_query(path)
        # Credentials must never enter browser history, referrers or URL logs.
        # POST forms use the body; raw OTA uploads use a dedicated header.
        if any(key in params for key in (
                "pw", "admin_password", "admin_pw", "sta_password",
                "sta_password_2", "sta_password_3", "ap_password",
                "cloud_device_token", "activation_token", "claim_code")):
            _api_error(conn, 400, "credentials_in_url", "Send credentials in the request body or OTA header, never the URL")
            return
        if method in (b"GET", b"HEAD") and route == "/update" and any(
                key in params for key in ("upload", "firmware_url")):
            _api_error(conn, 405, "method_not_allowed", "Firmware installation requires POST")
            return
        if route in ("/", "/calibration", "/update") and method not in (b"GET", b"HEAD", b"POST"):
            _api_error(conn, 405, "method_not_allowed", "Use GET or POST")
            return
        if route in ("/data", "/status", "/export.csv") and method not in (b"GET", b"HEAD"):
            _api_error(conn, 405, "method_not_allowed", "Use GET")
            return
        if route in ("/", "/calibration", "/update"):
            provided = _header_value(data, "x-knowco2-admin-password")
            if provided:
                params["pw"] = provided

        _is_ota_upload = (route == "/update" and "upload" in params)
        is_api = route == "/api/v1" or route.startswith("/api/v1/")
        api_payload = {}
        if method in (b"POST", b"PATCH") and not _is_ota_upload:
            content_length = _request_content_length(data)
            if content_length < 0:
                _api_error(conn, 400, "invalid_content_length", "Content-Length is invalid")
                return
            body_limit = 4096 if is_api else 8192
            if content_length > body_limit:
                _api_error(conn, 413, "payload_too_large", "Request body is too large")
                return
            post_body = read_request_body(
                conn, data, max_bytes=4096 if is_api else 8192
            )
            if len(post_body) != content_length:
                _api_error(conn, 400, "incomplete_body", "Request body is incomplete")
                return
            if is_api:
                content_type = _header_value(data, "content-type").lower()
                if "application/json" not in content_type:
                    _api_error(conn, 400, "json_required", "Content-Type must be application/json")
                    return
                try:
                    api_payload = json.loads(post_body.decode("utf-8"))
                except Exception:
                    _api_error(conn, 400, "invalid_json", "Request body must be valid JSON")
                    return
                if not isinstance(api_payload, dict):
                    _api_error(conn, 422, "invalid_request", "Request body must be an object")
                    return
            elif post_body:
                try:
                    post_body_str = post_body.decode("utf-8", "ignore")
                    for pair in post_body_str.split("&"):
                        if not pair:
                            continue
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                        else:
                            k, v = pair, ""
                        params[url_decode(k)] = url_decode(v)
                except Exception:
                    pass

        if is_api:
            if route == "/api/v1/info":
                if method not in (b"GET", b"HEAD"):
                    _api_error(conn, 405, "method_not_allowed", "Use GET")
                else:
                    handle_api_info_route(conn)
            elif route == "/api/v1/settings":
                handle_api_settings_route(conn, method, api_payload)
            elif route == "/api/v1/wifi":
                handle_api_wifi_route(conn, method, api_payload)
            elif route == "/api/v1/cloud/claim":
                handle_api_cloud_claim_route(conn, method, api_payload)
            elif route == "/api/v1/cloud/claim/clear":
                handle_api_cloud_clear_route(conn, method, api_payload)
            elif route == "/api/v1/cloud/credentials/forget":
                # Local-only forgetting. Ownership remains in the account and
                # must be released/revoked through the authenticated cloud API.
                handle_api_cloud_clear_route(conn, method, api_payload, forget=True)
            elif route == "/api/v1/cloud/enabled":
                handle_api_cloud_enabled_route(conn, method, api_payload)
            elif route == "/api/v1/onboarding":
                handle_api_onboarding_route(conn, method, api_payload)
            else:
                _api_error(conn, 404, "not_found", "Unknown local API route")
            return

        if route == "/data":
            handle_data_route(conn)
        elif route == "/status":
            handle_status_route(conn)
        elif route == "/export.csv":
            handle_export_csv_route(conn)
        elif route == "/calibration":
            handle_calibration_route(conn, params, method=method)
        elif route == "/update":
            handle_update_route(conn, params, method=method, raw_headers=data)
        elif route == "/":
            handle_root_route(conn, params, method=method)
        else:
            _api_error(conn, 404, "not_found", "Unknown local route")

    except Exception as e:
        log("http_err", "HTTP error:", e, min_interval=1.0)
    finally:
        runtime.clear_busy()
        try:
            conn.close()
        except Exception:
            pass
