# knowco2/telemetry/cloud.py
# ----------------------------------------------------------------------
# HTTPS cloud upload with HMAC-SHA256 auth.
#   * reuses one SocketPool + TLS Session (state.socket_pool / cloud_session)
#     to avoid "Out of sockets" on the ESP32-S3's small socket pool
#   * signs each post: base64(HMAC_SHA256(secret, f"{ts}.{body}"))
#   * headers: x-kc2-device-id / x-kc2-ts / x-kc2-sig
# ----------------------------------------------------------------------

import gc
import json
import time

from .. import state, config, runtime, crypto
from ..net import wifi as wifi_mod
from ..helpers import log, clamp_int

try:
    import wifi
    import socketpool
except ImportError:
    wifi = None
    socketpool = None

try:
    import ssl
    import adafruit_requests
except Exception as e:
    ssl = None
    adafruit_requests = None
    print("cloud deps IMPORT FAILED:", e)


def _get_session():
    if wifi is None or socketpool is None or ssl is None or adafruit_requests is None:
        return None

    if state.socket_pool is None:
        state.socket_pool = socketpool.SocketPool(wifi.radio)

    if state.cloud_ctx is None:
        state.cloud_ctx = ssl.create_default_context()

    if state.cloud_session is None:
        state.cloud_session = adafruit_requests.Session(state.socket_pool, state.cloud_ctx)

    return state.cloud_session


def cloud_next_interval():
    base = state.cloud_interval_sec
    backoff = base * (2 ** min(state.cloud_failures, 6))
    return clamp_int(backoff, 15, config.CLOUD_MAX_BACKOFF, backoff)


def cloud_send(payload_dict):
    if not state.cloud_enabled:
        return False

    if adafruit_requests is None or ssl is None or socketpool is None or wifi is None:
        log("cloud_deps", "Cloud deps missing (ssl/requests/socketpool/wifi)", min_interval=30.0)
        return False

    if not state.cloud_api_url or not state.cloud_device_token:
        return False

    if state.wifi_mode != config.WIFI_MODE_STA:
        return False

    if not wifi_mod.ensure_sta_connected():
        return False

    device_id = (state.settings.get("device_id") or "").strip()
    if not device_id:
        runtime.show_status("Cloud: no device_id")
        return False

    key_bytes = crypto.decode_token_to_bytes(state.cloud_device_token)
    if not key_bytes:
        runtime.show_status("Cloud: bad token")
        return False

    ts = int(time.time())
    state.cloud_last_attempt_ts = ts

    body = json.dumps(payload_dict, separators=(",", ":"))  # stable JSON for signing
    msg = (str(ts) + "." + body).encode("utf-8")

    mac = crypto.hmac_sha256_digest(key_bytes, msg)
    if not mac:
        runtime.show_status("Cloud: no crypto")
        return False

    sig_b64 = crypto.b64encode_bytes(mac)

    session = _get_session()
    if session is None:
        runtime.show_status("Cloud: no session")
        return False

    url = state.cloud_api_url.rstrip("/") + "/v1/ingest"
    headers = {
        "content-type": "application/json",
        "x-kc2-device-id": device_id,
        "x-kc2-ts": str(ts),
        "x-kc2-sig": sig_b64,
    }

    r = None
    if state._wd is not None:
        try:
            state._wd.feed()
        except Exception:
            pass
    try:
        r = session.post(url, data=body, headers=headers, timeout=8)

        resp_preview = ""
        try:
            resp_preview = (r.text or "")
        except Exception:
            resp_preview = ""
        if len(resp_preview) > 180:
            resp_preview = resp_preview[:180] + "..."
        log("cloud", "POST", url, "->", r.status_code, resp_preview, min_interval=0.0)

        code = int(r.status_code)
        state.cloud_last_http = code
        state.cloud_last_error = ""

        if code == 200:
            return True
        if code in (401, 403):
            runtime.show_status("Cloud: auth err")
            return False
        if code == 402:
            runtime.show_status("Cloud: inactive")
            return False

        runtime.show_status("Cloud HTTP %d" % code)
        return False

    except Exception as e:
        state.cloud_last_http = None
        state.cloud_last_error = repr(e)
        log("cloud", "cloud_send error:", e, min_interval=2.0)
        runtime.show_status("Cloud: fail")
        return False
    finally:
        try:
            if r:
                r.close()
        except Exception:
            pass
        try:
            gc.collect()
        except Exception:
            pass
        if state._wd is not None:
            try:
                state._wd.feed()
            except Exception:
                pass
