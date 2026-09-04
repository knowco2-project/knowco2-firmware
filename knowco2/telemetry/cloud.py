# knowco2/telemetry/cloud.py
# ----------------------------------------------------------------------
# HTTPS cloud upload with HMAC-SHA256 auth.
#   * reuses one SocketPool + TLS Session (state.socket_pool / cloud_session)
#     to avoid "Out of sockets" on the ESP32-S3's small socket pool
#   * signs each post: base64(HMAC_SHA256(secret, f"{ts}.{body}"))
#   * headers: x-kc2-device-id / x-kc2-ts / x-kc2-sig
#
# TLS MEMORY SELF-HEAL (RC-48)
# ----------------------------
# On the ESP32-S3, TLS handshakes are serviced by mbedTLS out of the
# ESP-IDF *internal* SRAM heap — NOT the CircuitPython gc heap (which
# lives in PSRAM). When internal heap runs short, socket/TLS creation
# raises a bare MemoryError() even though gc.mem_free() looks huge.
#
# Worse, adafruit_connection_manager (<= 3.1.8) creates the raw TCP
# socket *before* ssl_context.wrap_socket(); if wrap_socket raises
# MemoryError the raw socket is leaked — it is neither closed nor
# tracked, so the manager's own free/retry logic can never reclaim it.
# Each retry then leaks another LWIP socket + its internal-heap buffers,
# and cloud upload becomes permanently broken until reboot.
#
# Mitigations, in order:
#   1. Pre-flight: skip the TLS attempt when the internal heap's largest
#      free block is below CLOUD_TLS_MIN_LARGEST_BLOCK (handshake would
#      fail anyway, and a failed handshake *leaks*).
#   2. On MemoryError / RuntimeError: tear the session down via
#      connection_manager_close_all() + gc.collect() so every socket the
#      manager DOES track is closed and the next attempt starts clean.
#   3. Last resort: after CLOUD_MEMERR_RESET_AFTER consecutive
#      memory-class failures (and a minimum uptime guard), hard-reset the
#      MCU — mirrors the sensor hard-reset policy. Leaked sockets cannot
#      be reclaimed from Python; a reboot is the only full recovery.
# ----------------------------------------------------------------------

import gc
import json
import time

from .. import state, config, runtime, crypto
from .. import settings as settings_mod
from ..net import wifi as wifi_mod
from ..helpers import log, clamp_int
from ..web.api_v1_contract import (
    activation_request_payload,
    normalize_activation_response,
    normalize_claim_request,
)

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

try:
    import adafruit_connection_manager
except Exception:
    adafruit_connection_manager = None

try:
    import espidf  # internal (ESP-IDF) heap introspection; espressif-only
except Exception:
    espidf = None

try:
    import microcontroller
except Exception:
    microcontroller = None

try:
    import traceback
except Exception:
    traceback = None


def idf_heap_info():
    """Return (free_bytes, largest_free_block) of the ESP-IDF internal
    heap, or (None, None) when unavailable. This is the heap mbedTLS
    allocates from; gc.mem_free() (PSRAM) says nothing about it."""
    if espidf is None:
        return (None, None)
    try:
        return (espidf.heap_caps_get_free_size(),
                espidf.heap_caps_get_largest_free_block())
    except Exception:
        return (None, None)


def _record_failure(e):
    """Forensics for /status: full traceback of the failing line plus the
    heap picture AT the moment of failure (the /status-time numbers can
    look healthy even when the failing allocation could not be serviced)."""
    try:
        if traceback is not None:
            try:
                trace = "".join(traceback.format_exception(e))
            except TypeError:  # older CP signature
                trace = "".join(
                    traceback.format_exception(type(e), e, getattr(e, "__traceback__", None))
                )
        else:
            trace = repr(e)
        # keep the tail: innermost frames + the exception line
        state.cloud_last_trace = trace[-600:]
    except Exception:
        state.cloud_last_trace = repr(e)
    try:
        state.idf_free_at_fail, state.idf_largest_at_fail = idf_heap_info()
    except Exception:
        pass
    try:
        state.mem_free_at_fail = gc.mem_free()
    except Exception:
        pass


def teardown_session(reason=""):
    """Close every socket the connection manager tracks for our pool and
    drop the cached Session/SSLContext so the next send rebuilds from
    scratch. Safe to call any time; never raises."""
    if adafruit_connection_manager is not None and state.socket_pool is not None:
        try:
            adafruit_connection_manager.connection_manager_close_all(state.socket_pool)
        except Exception:
            pass
    state.cloud_session = None
    state.cloud_ctx = None
    try:
        gc.collect()
    except Exception:
        pass
    if reason:
        log("cloud", "TLS session torn down:", reason, min_interval=0.0)


def _maybe_memerr_reset():
    """Hard-reset the MCU after repeated memory-class cloud failures.
    Bounded: requires CLOUD_MEMERR_RESET_MIN_UPTIME_S of uptime so a
    persistent fault cannot become a reboot loop (safemode.py's NVM
    counter is the second backstop)."""
    if state.cloud_consec_memerr < config.CLOUD_MEMERR_RESET_AFTER:
        return
    if microcontroller is None:
        return
    try:
        now = time.monotonic()
        if (now - state.boot_time_mono) < config.CLOUD_MEMERR_RESET_MIN_UPTIME_S:
            return
        # Someone is actively using the web UI (status page, settings, an
        # OTA upload in progress): a persistent cloud fault means this
        # reset would fire every ~31 min, and rebooting mid-upload turns a
        # recovery attempt into "network error". Defer; cloud is already
        # down, so waiting costs nothing.
        if state.last_http_ts and (now - state.last_http_ts) < config.CLOUD_MEMERR_RESET_WEB_GRACE_S:
            log("cloud", "mem-reset deferred: web UI active", min_interval=30.0)
            return
    except Exception:
        return
    log("cloud", "persistent TLS MemoryError x%d — hard reset"
        % state.cloud_consec_memerr, min_interval=0.0)
    runtime.show_status("Cloud: mem reset")
    time.sleep(0.5)
    try:
        microcontroller.reset()
    except Exception:
        pass


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


def _device_serial():
    """Return the provisioned product serial when one is available."""
    for key in ("serial", "serial_number", "device_serial"):
        value = str(state.settings.get(key) or "").strip()
        if value:
            return value[:64]
    # Production tooling historically stored KC2 serials in device_id.
    value = str(state.settings.get("device_id") or "").strip()
    if value.upper().startswith("KC2-"):
        return value[:64]
    return None


def _activation_retry(reason):
    """Record a transient failure and schedule a bounded-rate retry."""
    state.cloud_activation_state = "pending"
    state.cloud_activation_error = reason
    state.cloud_activation_failures += 1
    exponent = min(state.cloud_activation_failures - 1, 5)
    delay = config.CLOUD_ACTIVATION_RETRY_MIN_S * (2 ** max(0, exponent))
    delay = min(delay, config.CLOUD_ACTIVATION_RETRY_MAX_S)
    state.cloud_activation_next_attempt = time.monotonic() + delay


def _clear_pending_claim(new_state, reason=""):
    """Delete the temporary bearer from RAM."""
    state.pending_cloud_claim = None
    state.cloud_activation_request_id = None
    state.cloud_activation_state = new_state
    state.cloud_activation_error = reason
    state.cloud_activation_next_attempt = 0.0
    if new_state != "pending":
        state.cloud_activation_failures = 0
    return True


def activate_pending_claim():
    """Exchange one queued claim for the permanent ingest credential.

    This function performs at most one HTTPS request with an eight-second
    timeout. The main loop invokes it only when its monotonic retry deadline is
    due, so an outage cannot create a tight loop or starve sensor sampling.
    Temporary credentials are never included in serial logs or status payloads.
    """
    pending = state.pending_cloud_claim
    if not pending:
        if state.cloud_device_token:
            state.cloud_activation_state = "configured"
        return False
    try:
        pending = normalize_claim_request(pending)
    except ValueError:
        _clear_pending_claim("error", "invalid_local_claim")
        return False

    if state.wifi_mode != config.WIFI_MODE_STA:
        state.cloud_activation_state = "pending"
        return False
    if time.monotonic() < state.cloud_activation_next_attempt:
        return False

    serial = _device_serial()
    if not wifi_mod.ensure_sta_connected():
        _activation_retry("network_unavailable")
        return False

    idf_free, idf_big = idf_heap_info()
    state.idf_free = idf_free
    state.idf_largest_block = idf_big
    if idf_big is not None and idf_big < config.CLOUD_TLS_MIN_LARGEST_BLOCK:
        teardown_session("activation internal heap low")
        _activation_retry("memory_unavailable")
        return False

    try:
        if not state.cloud_activation_request_id:
            # Backward defensive path; normal API queueing sets this once.
            from ..helpers import rand_token
            state.cloud_activation_request_id = rand_token(32)
        request_payload = activation_request_payload(
            temporary_credential=pending,
            hardware_id=state.hwid_hex,
            board_id=state.board_id_str,
            serial=serial,
            firmware_version=version_string(),
            request_id=state.cloud_activation_request_id,
        )
    except ValueError:
        _clear_pending_claim("error", "invalid_local_claim")
        return False

    session = _get_session()
    if session is None:
        _activation_retry("network_unavailable")
        return False

    body = json.dumps(request_payload, separators=(",", ":"))
    url = config.CLOUD_ACTIVATION_BASE_URL + config.CLOUD_ACTIVATION_PATH
    headers = {"content-type": "application/json"}
    response = None
    state.cloud_activation_state = "activating"
    if state._wd is not None:
        try:
            state._wd.feed()
        except Exception:
            pass

    try:
        response = session.post(
            url,
            data=body,
            headers=headers,
            timeout=config.CLOUD_ACTIVATION_TIMEOUT_S,
        )
        status = int(response.status_code)
        # Never log the response body: success contains the permanent secret
        # and error responses may include credential-derived diagnostics.
        log("activate", "cloud activation HTTP", status, min_interval=2.0)

        if status == 200:
            raw = response.text or ""
            if len(raw) > 2048:
                _activation_retry("invalid_cloud_response")
                return False
            try:
                activated = normalize_activation_response(json.loads(raw))
            except (ValueError, TypeError):
                _activation_retry("invalid_cloud_response")
                return False

            old = {
                "device_id": state.settings.get("device_id"),
                "cloud_device_id": state.settings.get("cloud_device_id"),
                "cloud_device_token": state.settings.get("cloud_device_token"),
                "cloud_api_url": state.settings.get("cloud_api_url"),
                "cloud_enabled": state.settings.get("cloud_enabled"),
            }
            # Preserve the manufacturing/local ID. Cloud's operational ID is
            # a separate namespace and is used for ingest after activation.
            state.settings["cloud_device_id"] = activated["device_id"]
            state.settings["cloud_device_token"] = activated["device_secret"]
            # Activation credentials are sent only to the compiled origin and
            # ingest remains pinned there even if a malformed response tries to
            # redirect the device elsewhere.
            returned_url = activated.get("cloud_api_url", "").rstrip("/")
            if returned_url == config.CLOUD_ACTIVATION_BASE_URL:
                state.settings["cloud_api_url"] = returned_url
            else:
                state.settings["cloud_api_url"] = config.CLOUD_ACTIVATION_BASE_URL
            state.settings["cloud_enabled"] = True

            if not settings_mod.save_settings():
                for key, value in old.items():
                    state.settings[key] = value
                settings_mod.apply_settings()
                _activation_retry("storage_unavailable")
                return False

            settings_mod.apply_settings()
            state.pending_cloud_claim = None
            state.cloud_activation_request_id = None
            state.cloud_activation_state = "configured"
            state.cloud_activation_error = ""
            state.cloud_activation_failures = 0
            state.cloud_activation_next_attempt = 0.0
            runtime.show_status("Cloud: activated")
            return True

        # The credential is invalid, expired, already consumed, or forbidden.
        # Retrying would both retain a bearer unnecessarily and add load.
        if status in (400, 401, 403, 404, 409, 410, 422):
            _clear_pending_claim("error", "claim_rejected")
            runtime.show_status("Cloud: pairing expired")
            return False

        # Rate limits and service failures are transient; retain the claim but
        # back off. A cloud 410 response is the authoritative expiry signal.
        _activation_retry("cloud_unavailable")
        return False

    except (MemoryError, RuntimeError) as exc:
        teardown_session("activation transport reset")
        _record_failure(exc)
        _activation_retry("memory_unavailable")
        return False
    except Exception:
        _activation_retry("network_unavailable")
        return False
    finally:
        try:
            if response is not None:
                response.close()
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


def version_string():
    # Local import avoids making version part of the cloud module's startup
    # dependency chain on older release bundles.
    try:
        from .. import version
        return version.FIRMWARE_VERSION
    except Exception:
        return "unknown"


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

    device_id = (state.cloud_device_id or state.settings.get("device_id") or "").strip()
    if not device_id:
        runtime.show_status("Cloud: no device_id")
        return False

    key_bytes = crypto.decode_token_to_bytes(state.cloud_device_token)
    if not key_bytes:
        runtime.show_status("Cloud: bad token")
        return False

    # Pre-flight: refuse to start a TLS handshake the internal heap cannot
    # service — a failed wrap_socket LEAKS the underlying raw socket
    # (adafruit_connection_manager <= 3.1.8), making things strictly worse.
    idf_free, idf_big = idf_heap_info()
    state.idf_free = idf_free
    state.idf_largest_block = idf_big
    if idf_big is not None and idf_big < config.CLOUD_TLS_MIN_LARGEST_BLOCK:
        state.cloud_last_http = None
        state.cloud_last_error = "IDF heap low (largest free block %d B)" % idf_big
        state.cloud_consec_memerr += 1
        log("cloud", "skipping TLS: internal heap low", idf_free, idf_big, min_interval=5.0)
        teardown_session("internal heap low")
        _maybe_memerr_reset()
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
        try:
            r = session.post(url, data=body, headers=headers, timeout=8)
        except (MemoryError, RuntimeError):
            # RC-49: mem-class failures here are usually transient LWIP
            # internal-SRAM pressure from web-UI connection churn
            # (TIME_WAIT sockets). Slots drain continuously, so tear
            # down, breathe, and retry ONCE before counting a failure.
            teardown_session("retrying after mem-class failure")
            if state._wd is not None:
                try:
                    state._wd.feed()
                except Exception:
                    pass
            # Sliced sleep: keep buttons responsive and the watchdog fed
            # during the drain pause (RC-50).
            _pause_end = time.monotonic() + config.CLOUD_MEM_RETRY_PAUSE_S
            while time.monotonic() < _pause_end:
                runtime.poll_buttons()
                if state._wd is not None:
                    try:
                        state._wd.feed()
                    except Exception:
                        pass
                time.sleep(0.1)
            if state._wd is not None:
                try:
                    state._wd.feed()
                except Exception:
                    pass
            session = _get_session()
            if session is None:
                raise
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
        state.cloud_consec_memerr = 0

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

    except (MemoryError, RuntimeError) as e:
        # MemoryError: ESP-IDF internal heap could not service the
        # socket/TLS allocation (raised as a bare MemoryError()).
        # RuntimeError: typically "Out of sockets" from the pool.
        # Both are memory-class faults: tear down + count toward reset.
        state.cloud_last_http = None
        state.cloud_last_error = repr(e)
        _record_failure(e)
        state.cloud_consec_memerr += 1
        log("cloud", "cloud_send mem-class error:", e,
            "(consecutive: %d)" % state.cloud_consec_memerr, min_interval=2.0)
        runtime.show_status("Cloud: mem fail")
        teardown_session(repr(e))
        _maybe_memerr_reset()
        return False

    except Exception as e:
        state.cloud_last_http = None
        state.cloud_last_error = repr(e)
        _record_failure(e)
        log("cloud", "cloud_send error:", e, min_interval=2.0)
        runtime.show_status("Cloud: fail")
        return False
    finally:
        try:
            if r is not None:
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
