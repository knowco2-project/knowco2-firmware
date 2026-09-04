# knowco2/usb_api.py
# -----------------------------------------------------------------------------
# Versioned, non-blocking USB data protocol for KnowCO2.
#
# Transport: CircuitPython usb_cdc.data
# Framing:   one UTF-8 JSON object per line (NDJSON)
# Protocol:  version 1
#
# This first hardware-test slice intentionally starts read-mostly. It exposes
# discovery, status, history, settings metadata, unlock state, identify, ping,
# and live samples. Mutating settings/calibration/network commands can be added
# iteratively on this PR after the transport is proven on physical devices.
# -----------------------------------------------------------------------------

import gc
import json
import time

from . import config, runtime, state, version

try:
    import usb_cdc
except Exception:
    usb_cdc = None


PROTOCOL_VERSION = 1
TRANSPORT_NAME = "usb-cdc-ndjson"
MAX_RX_BUFFER = 4096
MAX_RX_LINE = 2048
MAX_READ_PER_POLL = 512
MAX_WRITE_PER_POLL = 512
MAX_COMMANDS_PER_POLL = 2
MAX_RESPONSE_QUEUE = 8
MAX_HISTORY_POINTS = 256

_port = None
_rx = bytearray()
_response_queue = []
_tx_active = None
_tx_offset = 0
_sample_pending = None
_stream_enabled = False
_was_connected = False
_sequence = 0
_last_published_sample_ts = None
_coalesced_samples = 0


CAPABILITIES = {
    "sample_stream": True,
    "status": True,
    "history": True,
    "settings_read": True,
    "settings_patch": False,
    "sensor_compensation": False,
    "forced_calibration": False,
    "wifi_provisioning": False,
    "cloud_provisioning": False,
    "identify": True,
    "reboot": False,
    "debug": True,
    "development_slice": "transport-readonly-v1",
}


PUBLIC_SETTING_KEYS = (
    "low_threshold",
    "med_threshold",
    "alert_threshold",
    "alerts_enabled",
    "graph_scale_mode",
    "max_points",
    "temp_mode",
    "display_mode",
    "display_flip",
    "colorblind_mode",
    "dim_enabled",
    "dim_start_hour",
    "dim_end_hour",
    "dim_brightness",
    "lang",
    "energy_mode",
    "asc_enabled",
    "altitude",
    "ambient_pressure",
)


def init():
    """Attach to usb_cdc.data and configure cooperative non-blocking I/O."""
    global _port
    if _port is not None:
        return True
    if usb_cdc is None:
        return False
    try:
        _port = usb_cdc.data
    except Exception:
        _port = None
    if _port is None:
        return False
    try:
        _port.timeout = 0
    except Exception:
        pass
    try:
        _port.write_timeout = 0
    except Exception:
        pass
    return True


def available():
    return init() and _port is not None


def connected():
    if not available():
        return False
    try:
        return bool(_port.connected)
    except Exception:
        return True


def _reset_session():
    global _rx, _response_queue, _tx_active, _tx_offset
    global _sample_pending, _stream_enabled, _coalesced_samples
    _rx = bytearray()
    _response_queue = []
    _tx_active = None
    _tx_offset = 0
    _sample_pending = None
    _stream_enabled = False
    _coalesced_samples = 0


def _json_bytes(obj):
    try:
        return (json.dumps(obj) + "\n").encode("utf-8")
    except Exception:
        return None


def _error(code, message, details=None):
    value = {"code": code, "message": message}
    if details is not None:
        value["details"] = details
    return value


def _queue_frame(frame):
    if frame is None or len(_response_queue) >= MAX_RESPONSE_QUEUE:
        return False
    _response_queue.append(frame)
    return True


def _queue_response(request_id, result=None, error=None):
    message = {
        "v": PROTOCOL_VERSION,
        "type": "response",
        "id": request_id,
        "ok": error is None,
    }
    if error is None:
        message["result"] = result if result is not None else {}
    else:
        message["error"] = error
    return _queue_frame(_json_bytes(message))


def _queue_event(name, data):
    return _queue_frame(_json_bytes({
        "v": PROTOCOL_VERSION,
        "type": "event",
        "event": name,
        "data": data,
    }))


def _round_or_none(value, digits=2):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _quality(co2):
    if co2 is None:
        return "unknown"
    try:
        value = float(co2)
    except Exception:
        return "unknown"
    if value < state.LOW_THRESHOLD:
        return "good"
    if value < state.MED_THRESHOLD:
        return "elevated"
    if value < state.ALERT_THRESHOLD:
        return "poor"
    return "alert"


def _unlock_remaining_s():
    try:
        return max(0, int(state.ota_unlock_until - time.monotonic()))
    except Exception:
        return 0


def _identity_payload():
    return {
        "protocol_version": PROTOCOL_VERSION,
        "transport": TRANSPORT_NAME,
        "device_id": state.settings.get("device_id", "co2-node-1"),
        "hwid": state.hwid_hex,
        "board_id": state.board_id_str,
        "pair_code": state.pair_code,
        "firmware_version": version.FIRMWARE_VERSION,
        "circuitpython_version": version.CP_VERSION,
        "sensor_model": state.sensor_model_str,
        "sensor_serial": state.scd_serial_str,
        "capabilities": CAPABILITIES,
    }


def _sample_payload(sequence=None):
    try:
        uptime_ms = int((time.monotonic() - state.boot_time_mono) * 1000)
    except Exception:
        uptime_ms = None
    unix_time = None
    if state.ntp_synced:
        try:
            unix_time = int(time.time())
        except Exception:
            pass
    return {
        "device_id": state.settings.get("device_id", "co2-node-1"),
        "hwid": state.hwid_hex,
        "seq": _sequence if sequence is None else sequence,
        "unix_time": unix_time,
        "time_valid": bool(state.ntp_synced),
        "uptime_ms": uptime_ms,
        "co2_ppm": int(state.last_co2) if state.last_co2 is not None else None,
        "temperature_c": _round_or_none(state.last_temp_c, 2),
        "relative_humidity_pct": _round_or_none(state.last_rh, 2),
        "rate_of_change_ppm_s": _round_or_none(state.rate_of_change, 3),
        "quality": _quality(state.last_co2),
        "sample_period_s": _round_or_none(state._scd_period_effective, 1),
        "battery_v": _round_or_none(state.cached_vbat, 3),
        "battery_pct": int(state.cached_pct) if state.cached_pct is not None else None,
        "energy_mode": bool(state.energy_mode),
    }


def _public_settings_payload():
    result = {}
    for key in PUBLIC_SETTING_KEYS:
        if key == "low_threshold":
            result[key] = state.LOW_THRESHOLD
        elif key == "med_threshold":
            result[key] = state.MED_THRESHOLD
        elif key == "alert_threshold":
            result[key] = state.ALERT_THRESHOLD
        elif key == "alerts_enabled":
            result[key] = bool(state.alerts_enabled)
        elif key == "temp_mode":
            result[key] = state.temp_mode
        elif key == "display_mode":
            result[key] = state.display_mode
        elif key == "energy_mode":
            result[key] = bool(state.energy_mode)
        else:
            result[key] = state.settings.get(key)
    return result


def _status_payload():
    now = time.monotonic()
    try:
        sensor_age = max(0.0, now - state.last_scd_sample_ts)
        sensor_timeout = max(config.SCD_SAMPLE_TIMEOUT,
                             state._scd_period_effective * 2.5)
    except Exception:
        sensor_age = None
        sensor_timeout = config.SCD_SAMPLE_TIMEOUT
    try:
        mem_free = gc.mem_free()
        mem_alloc = gc.mem_alloc()
    except Exception:
        mem_free = None
        mem_alloc = None
    try:
        trend_arrow = runtime.compute_trend_arrow()
    except Exception:
        trend_arrow = None

    return {
        "identity": _identity_payload(),
        "sample": _sample_payload(),
        "display": {
            "mode": state.display_mode,
            "temperature_unit": state.temp_mode,
            "alerts_enabled": bool(state.alerts_enabled),
            "trend_arrow": trend_arrow,
            "thresholds_ppm": {
                "low": state.LOW_THRESHOLD,
                "medium": state.MED_THRESHOLD,
                "alert": state.ALERT_THRESHOLD,
            },
        },
        "sensor": {
            "model": state.sensor_model_str,
            "serial": state.scd_serial_str,
            "ok": sensor_age is not None and sensor_age <= sensor_timeout,
            "last_sample_age_s": _round_or_none(sensor_age, 1),
            "crc_failures": state.scd_crc_failures,
            "recoveries": state.scd_recoveries,
            "asc_enabled": bool(state.settings.get("asc_enabled", True)),
            "altitude_m": int(state.settings.get("altitude", 0) or 0),
            "ambient_pressure_hpa": int(state.settings.get("ambient_pressure", 0) or 0),
        },
        "network": {
            "wifi_mode": state.wifi_mode,
            "ip": state.ip_str_cached,
            "mdns": (state.mdns_hostname + ".local") if state.mdns_hostname else None,
            "ntp_synced": bool(state.ntp_synced),
            "cloud_enabled": bool(state.cloud_enabled),
            "cloud_last_http": state.cloud_last_http,
            "cloud_failures": state.cloud_failures,
        },
        "system": {
            "fs_readonly": bool(state.fs_readonly),
            "safe_mode_recoveries": state.safe_mode_recoveries,
            "memory_free": mem_free,
            "memory_alloc": mem_alloc,
            "unlock_remaining_s": _unlock_remaining_s(),
        },
        "usb": {
            "stream_enabled": bool(_stream_enabled),
            "coalesced_samples": _coalesced_samples,
            "protocol_version": PROTOCOL_VERSION,
        },
    }


def _history_payload(params):
    try:
        limit = int(params.get("limit", 120))
    except Exception:
        return None, _error("invalid_limit", "limit must be an integer")
    limit = max(1, min(MAX_HISTORY_POINTS, limit))
    values = []
    for point in state.co2_history[-limit:]:
        try:
            values.append(int(point) if point is not None else None)
        except Exception:
            values.append(None)
    return {
        "period_s": _round_or_none(state._scd_period_effective, 1),
        "newest_first": False,
        "co2_ppm": values,
        "latest_temperature_c": _round_or_none(state.last_temp_c, 2),
        "latest_relative_humidity_pct": _round_or_none(state.last_rh, 2),
    }, None


def _dispatch(method, params):
    global _stream_enabled
    if method == "hello":
        _stream_enabled = bool(params.get("stream", False))
        result = _identity_payload()
        result["stream_enabled"] = _stream_enabled
        return result, None
    if method == "ping":
        return {
            "uptime_s": int(time.monotonic() - state.boot_time_mono),
            "stream_enabled": bool(_stream_enabled),
        }, None
    if method == "status.get":
        return _status_payload(), None
    if method == "history.get":
        return _history_payload(params)
    if method == "settings.get":
        return _public_settings_payload(), None
    if method == "stream.set":
        _stream_enabled = bool(params.get("enabled", True))
        return {"enabled": _stream_enabled}, None
    if method == "unlock.status":
        return {
            "remaining_s": _unlock_remaining_s(),
            "admin_password_configured": bool(state.settings.get("admin_password", "")),
        }, None
    if method == "device.identify":
        label = str(params.get("host", "Linux host"))[:32]
        runtime.show_status("Connected: " + label)
        return {"identified": True}, None
    return None, _error("method_not_found", "Unknown method: " + str(method))


def _handle_line(line):
    request_id = None
    try:
        text = line.decode("utf-8").strip()
        if not text:
            return
        request = json.loads(text)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        request_id = request.get("id")
        if request.get("v", PROTOCOL_VERSION) != PROTOCOL_VERSION:
            _queue_response(request_id, error=_error(
                "unsupported_version", "Unsupported protocol version",
                {"supported": [PROTOCOL_VERSION]}))
            return
        method = request.get("method")
        if not isinstance(method, str) or not method:
            raise ValueError("method must be a non-empty string")
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        result, error = _dispatch(method, params)
        _queue_response(request_id, result=result, error=error)
    except ValueError as exc:
        _queue_response(request_id, error=_error("invalid_request", str(exc)))
    except Exception as exc:
        _queue_response(request_id, error=_error("internal_error", str(exc)))


def _read_and_dispatch():
    if _port is None:
        return
    try:
        waiting = int(_port.in_waiting)
    except Exception:
        waiting = 0
    if waiting > 0:
        try:
            data = _port.read(min(waiting, MAX_READ_PER_POLL))
        except Exception:
            data = None
        if data:
            _rx.extend(data)
            if len(_rx) > MAX_RX_BUFFER:
                _rx[:] = b""
                _queue_event("protocol.error", _error(
                    "rx_overflow", "USB receive buffer overflow; frame discarded"))

    commands = 0
    while commands < MAX_COMMANDS_PER_POLL:
        newline = _rx.find(b"\n")
        if newline < 0:
            break
        line = bytes(_rx[:newline])
        del _rx[:newline + 1]
        if len(line) > MAX_RX_LINE:
            _queue_event("protocol.error", _error(
                "frame_too_large", "USB request exceeded maximum frame size"))
        else:
            _handle_line(line.rstrip(b"\r"))
        commands += 1


def _flush_tx():
    global _tx_active, _tx_offset, _sample_pending
    if _port is None:
        return
    if _tx_active is None:
        if _response_queue:
            _tx_active = _response_queue.pop(0)
        elif _sample_pending is not None:
            _tx_active = _sample_pending
            _sample_pending = None
        else:
            return
        _tx_offset = 0

    end = min(len(_tx_active), _tx_offset + MAX_WRITE_PER_POLL)
    chunk = _tx_active[_tx_offset:end]
    try:
        written = _port.write(chunk)
    except Exception:
        return
    if written is None:
        written = len(chunk)
    if written <= 0:
        return
    _tx_offset += written
    if _tx_offset >= len(_tx_active):
        _tx_active = None
        _tx_offset = 0


def _publish_latest_sample_if_new():
    """Detect successful sensor commits without coupling code.py to USB."""
    global _last_published_sample_ts, _sequence, _sample_pending
    global _coalesced_samples
    if not _stream_enabled or state.last_co2 is None:
        return
    sample_ts = state.last_scd_sample_ts
    if sample_ts == _last_published_sample_ts:
        return
    _last_published_sample_ts = sample_ts
    _sequence = (_sequence + 1) & 0x7FFFFFFF
    payload = _sample_payload(_sequence)
    if _sample_pending is not None:
        _coalesced_samples += 1
        payload["coalesced_samples"] = _coalesced_samples
    _sample_pending = _json_bytes({
        "v": PROTOCOL_VERSION,
        "type": "event",
        "event": "sample",
        "data": payload,
    })


def poll():
    """Service USB briefly. Safe to call on every main-loop pass."""
    global _was_connected
    if not available():
        return
    is_connected = connected()
    if not is_connected:
        if _was_connected:
            _reset_session()
        _was_connected = False
        return
    if not _was_connected:
        _reset_session()
        _was_connected = True
        _queue_event("ready", _identity_payload())
    _publish_latest_sample_if_new()
    _read_and_dispatch()
    _flush_tx()
