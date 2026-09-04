# knowco2/web/api_v1_contract.py
# ----------------------------------------------------------------------
# Pure helpers for the platform-neutral KnowCO2 Local Device API v1.
#
# This module intentionally has no CircuitPython-only imports so the contract
# can be unit tested on CPython and reused by the browser UI, iOS, Android,
# macOS, Windows, and future clients.
# ----------------------------------------------------------------------

import binascii


API_SCHEMA_VERSION = 1
CLAIM_CODE_LENGTH = 8
CLAIM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TRUSTED_CLOUD_ORIGIN = "https://api.knowco2.com"

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

WRITE_SETTING_KEYS = (
    "temp_mode",
    "display_mode",
    "alerts_enabled",
    "low_threshold",
    "med_threshold",
    "alert_threshold",
    "colorblind_mode",
    "cloud_interval_sec",
    "asc_enabled",
    "altitude",
    "ambient_pressure",
)

SENSITIVE_SETTING_KEYS = (
    "sta_password",
    "sta_password_2",
    "sta_password_3",
    "ap_password",
    "cloud_device_token",
    "cloud_device_secret",
    "device_secret",
    "admin_password",
    "pending_cloud_claim",
    "activation_token",
    "claim_code",
)


def safe_settings(settings):
    """Return the public settings subset without any credentials."""
    src = settings or {}
    out = {}
    for key in PUBLIC_SETTING_KEYS:
        if key in src:
            out[key] = src.get(key)
    return out


def info_payload(*, device_id=None, cloud_device_id=None, serial=None, board_id=None,
                 firmware_version=None, cp_version=None, wifi_mode=None,
                 mdns=None, wifi_configured=False, cloud_configured=False,
                 cloud_enabled=False, activation_state="unconfigured"):
    """Build the stable GET /api/v1/info response payload.

    Temporary claim credentials and permanent device secrets are deliberately
    not accepted by this builder, which makes accidental reflection impossible.
    """
    return {
        "schema_version": API_SCHEMA_VERSION,
        "product": "KnowCO2",
        "device_id": device_id,
        "serial": serial,
        "board_id": board_id,
        "firmware_version": firmware_version,
        "circuitpython_version": cp_version,
        "setup": {
            "wifi_mode": wifi_mode,
            "wifi_configured": bool(wifi_configured),
            "mdns": mdns,
        },
        "cloud": {
            "device_id": cloud_device_id,
            "configured": bool(cloud_configured),
            "enabled": bool(cloud_enabled),
            "activation_state": activation_state,
        },
        "capabilities": {
            "local_api": 1,
            "browser_setup": True,
            "native_app_required": False,
            "wifi_write_only": True,
            "cloud_claim_write_only": True,
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

    for key in ("alerts_enabled", "colorblind_mode", "asc_enabled"):
        if key in out:
            value = out[key]
            if isinstance(value, bool):
                out[key] = value
            elif value in (0, 1):
                out[key] = bool(value)
            elif isinstance(value, str) and value.lower() in ("true", "false"):
                out[key] = value.lower() == "true"
            else:
                raise ValueError("%s must be a boolean" % key)

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
    # v1 intentionally mirrors the existing firmware connection stack, which
    # supports WPA/WPA2 personal networks. Open networks are not silently
    # accepted because they would be saved and then never attempted at boot.
    if len(password) < 8:
        raise ValueError("password must be 8-63 characters")
    return {"ssid": ssid, "password": password}


def normalize_claim_request(payload):
    """Validate a write-only cloud claim request.

    Human claim codes are normalized so ``ABCD-EFGH`` and ``abcd efgh`` are
    equivalent. App activation tokens are opaque and remain case-sensitive.
    Exactly one credential is allowed to prevent ambiguous server behavior.
    """
    if not isinstance(payload, dict):
        raise ValueError("cloud claim must be an object")

    claim_code = payload.get("claim_code")
    activation_token = payload.get("activation_token")
    if bool(claim_code) == bool(activation_token):
        raise ValueError("provide exactly one cloud claim credential")

    if claim_code:
        code = str(claim_code).replace("-", "").replace(" ", "").upper()
        if len(code) != CLAIM_CODE_LENGTH:
            raise ValueError("claim_code must contain 8 characters")
        for char in code:
            if char not in CLAIM_CODE_ALPHABET:
                raise ValueError("claim_code contains unsupported characters")
        return {"claim_code": code}

    token = str(activation_token or "")
    if len(token) < 32 or len(token) > 512:
        raise ValueError("activation_token length is invalid")
    for char in token:
        if not ("A" <= char <= "Z" or "a" <= char <= "z"
                or "0" <= char <= "9" or char in "-_"):
            raise ValueError("activation_token contains unsupported characters")
    return {"activation_token": token}


def activation_request_payload(*, temporary_credential, hardware_id=None,
                               board_id=None, serial=None,
                               firmware_version=None, request_id=None):
    """Build the cloud ``POST /v1/devices/activate`` request body."""
    credential = normalize_claim_request(temporary_credential)
    request_id = str(request_id or "")
    if len(request_id) != 64:
        raise ValueError("activation request_id is invalid")
    for char in request_id:
        if not ("0" <= char <= "9" or "A" <= char <= "F"):
            raise ValueError("activation request_id is invalid")
    result = {
        "schema_version": API_SCHEMA_VERSION,
        "request_id": request_id,
        "hardware_id": hardware_id,
        "board_id": board_id,
        "serial": serial,
        "firmware_version": firmware_version,
    }
    result.update(credential)
    return result


def normalize_activation_response(payload):
    """Validate cloud activation success without retaining extra fields."""
    if not isinstance(payload, dict):
        raise ValueError("activation response must be an object")
    if payload.get("schema_version") != API_SCHEMA_VERSION:
        raise ValueError("unsupported activation response version")

    device_id = str(payload.get("device_id") or "").strip()
    if not device_id or len(device_id) > 64:
        raise ValueError("activation response device_id is invalid")
    for char in device_id:
        if not ("A" <= char <= "Z" or "a" <= char <= "z"
                or "0" <= char <= "9" or char in "-_.:"):
            raise ValueError("activation response device_id is invalid")

    device_secret = str(payload.get("device_secret") or "").strip()
    # The ingest HMAC key is exactly 256 bits. Enforce canonical standard
    # base64 here so a malformed success response can never be persisted and
    # leave the device apparently configured but unable to authenticate.
    if len(device_secret) != 44:
        raise ValueError("activation response device_secret is invalid")
    try:
        decoded_secret = binascii.a2b_base64(device_secret)
        canonical_secret = binascii.b2a_base64(decoded_secret).decode("ascii").strip()
    except Exception:
        raise ValueError("activation response device_secret is invalid")
    if len(decoded_secret) != 32 or canonical_secret != device_secret:
        raise ValueError("activation response device_secret is invalid")

    result = {"device_id": device_id, "device_secret": device_secret}
    cloud_api_url = str(payload.get("cloud_api_url") or "").strip()
    if cloud_api_url:
        if cloud_api_url.rstrip("/") != TRUSTED_CLOUD_ORIGIN:
            raise ValueError("activation response cloud_api_url is invalid")
        result["cloud_api_url"] = TRUSTED_CLOUD_ORIGIN
    return result


def setup_qr_payload(device_id, ssid, ap_password):
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
        "local_url": "http://192.168.4.1",
    }
