# knowco2/web/api_v1_contract.py
# ----------------------------------------------------------------------
# Pure helpers for the platform-neutral KnowCO2 Local Device API v1.
#
# This module intentionally has no CircuitPython-only imports so the contract
# can be unit tested on CPython and reused by the browser UI, iOS, Android,
# macOS, Windows, and future clients.
# ----------------------------------------------------------------------

API_SCHEMA_VERSION = 1

# Settings that clients may read/write through the public local settings API.
# Secrets (Wi-Fi passwords, AP passwords, cloud device secrets/tokens, admin
# password) are deliberately absent.
PUBLIC_SETTING_KEYS = (
    "temp_mode",
    "display_mode",
    "alerts_enabled",
    "low_threshold",
    "med_threshold",
    "alert_threshold",
    "colorblind_mode",
    "cloud_enabled",
    "cloud_interval_sec",
    "asc_enabled",
    "altitude",
    "ambient_pressure",
)

WRITE_SETTING_KEYS = frozenset(PUBLIC_SETTING_KEYS)

SENSITIVE_SETTING_KEYS = frozenset((
    "sta_password",
    "sta_password_2",
    "sta_password_3",
    "ap_password",
    "cloud_device_token",
    "cloud_device_secret",
    "device_secret",
    "admin_password",
))


def safe_settings(settings):
    """Return the public settings subset without any credentials."""
    src = settings or {}
    out = {}
    for key in PUBLIC_SETTING_KEYS:
        if key in src:
            out[key] = src.get(key)
    return out


def info_payload(*, device_id=None, serial=None, hwid=None, board_id=None,
                 firmware_version=None, cp_version=None, pair_code=None,
                 wifi_mode=None, mdns=None, cloud_configured=False,
                 cloud_enabled=False):
    """Build the stable GET /api/v1/info response payload.

    pair_code is a short-lived setup confirmation value; clients MUST NOT use
    it as a permanent ownership credential.
    """
    return {
        "schema_version": API_SCHEMA_VERSION,
        "product": "KnowCO2",
        "device_id": device_id,
        "serial": serial,
        "hwid": hwid,
        "board_id": board_id,
        "firmware_version": firmware_version,
        "circuitpython_version": cp_version,
        "setup": {
            "pair_code": pair_code,
            "wifi_mode": wifi_mode,
            "mdns": mdns,
        },
        "cloud": {
            "configured": bool(cloud_configured),
            "enabled": bool(cloud_enabled),
        },
        "capabilities": {
            "local_api": 1,
            "browser_setup": True,
            "native_app_required": False,
        },
    }


def settings_payload(settings, settings_version=0):
    return {
        "schema_version": API_SCHEMA_VERSION,
        "settings_version": int(settings_version or 0),
        "settings": safe_settings(settings),
    }


def normalize_settings_patch(patch):
    """Validate a settings PATCH body and return the allowed subset.

    This is deliberately strict. Unknown keys are rejected instead of silently
    ignored so a version mismatch is visible to the client.
    """
    if not isinstance(patch, dict):
        raise ValueError("settings patch must be an object")

    unknown = [key for key in patch if key not in WRITE_SETTING_KEYS]
    if unknown:
        raise ValueError("unsupported setting: %s" % unknown[0])

    if any(key in SENSITIVE_SETTING_KEYS for key in patch):
        raise ValueError("sensitive settings are write-only through dedicated endpoints")

    out = dict(patch)

    if "temp_mode" in out:
        value = str(out["temp_mode"]).upper()
        if value not in ("F", "C"):
            raise ValueError("temp_mode must be F or C")
        out["temp_mode"] = value

    if "display_mode" in out:
        value = int(out["display_mode"])
        if value < 0 or value > 2:
            raise ValueError("display_mode must be 0, 1, or 2")
        out["display_mode"] = value

    for key in ("alerts_enabled", "colorblind_mode", "cloud_enabled", "asc_enabled"):
        if key in out:
            out[key] = bool(out[key])

    for key in ("low_threshold", "med_threshold", "alert_threshold"):
        if key in out:
            value = int(out[key])
            if value < 400 or value > 10000:
                raise ValueError("%s out of range" % key)
            out[key] = value

    low = int(out.get("low_threshold", 0) or 0)
    med = int(out.get("med_threshold", 0) or 0)
    alert = int(out.get("alert_threshold", 0) or 0)
    if low and med and low >= med:
        raise ValueError("low_threshold must be below med_threshold")
    if med and alert and med >= alert:
        raise ValueError("med_threshold must be below alert_threshold")

    if "cloud_interval_sec" in out:
        value = int(out["cloud_interval_sec"])
        if value < 15 or value > 3600:
            raise ValueError("cloud_interval_sec must be 15-3600")
        out["cloud_interval_sec"] = value

    if "altitude" in out:
        value = int(out["altitude"])
        if value < 0 or value > 3000:
            raise ValueError("altitude out of range")
        out["altitude"] = value

    if "ambient_pressure" in out:
        value = int(out["ambient_pressure"])
        if value != 0 and (value < 700 or value > 1200):
            raise ValueError("ambient_pressure out of range")
        out["ambient_pressure"] = value

    return out


def wifi_write_payload(ssid, password):
    """Validate the dedicated write-only Wi-Fi credential request.

    Returned values are intended for immediate application only and must never
    be echoed by an API response or written to logs.
    """
    ssid = str(ssid or "").strip()
    password = str(password or "")
    if not ssid or len(ssid) > 32:
        raise ValueError("ssid must be 1-32 characters")
    if len(password) > 63:
        raise ValueError("password must be at most 63 characters")
    if password and len(password) < 8:
        raise ValueError("password must be empty or at least 8 characters")
    return {"ssid": ssid, "password": password}


def setup_qr_payload(device_id, ssid, ap_password, pair_code=None):
    """Build a compact payload for native-app assisted setup.

    This carries only the temporary local AP credential. It MUST NEVER contain
    the permanent cloud HMAC secret.
    """
    if not device_id:
        raise ValueError("device_id required")
    if not ssid:
        raise ValueError("ssid required")
    return {
        "v": API_SCHEMA_VERSION,
        "type": "knowco2-setup",
        "device_id": str(device_id),
        "ssid": str(ssid),
        "ap_password": str(ap_password or ""),
        "pair_code": str(pair_code or ""),
        "local_url": "http://192.168.4.1",
    }
