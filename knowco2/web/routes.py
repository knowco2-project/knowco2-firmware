# knowco2/web/routes.py
# ----------------------------------------------------------------------
# HTTP route handlers, the tiny raw-socket server loop, and the OTA update
# flow (single .py file or full .zip package with STORED/DEFLATE support).
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

from .. import state, config, version, runtime
from .. import settings as settings_mod
from ..net import wifi as wifi_mod
from ..helpers import log, as_int, clamp_int
from . import portal_page
from .http_util import (
    send_all, build_response, make_json_response, make_html_response,
    parse_query, url_decode, read_request_head, read_request_body,
    stream_request_body_to_file, CAPTIVE_PATHS_204,
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
    header, body = make_json_response({"co2": ints})
    send_all(conn, header)
    send_all(conn, body)


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

        "hwid": state.hwid_hex,
        "board_id": state.board_id_str,
        "scd_serial": state.scd_serial_str,
        "pair_code": state.pair_code,
        "firmware_version": version.FIRMWARE_VERSION,
        "cp_version": version.CP_VERSION,

        "battery_v": vbat,
        "battery_pct": pct,
        "battery_gauge": state.fuel_gauge_kind,
        "battery_bus": state.fuel_bus_name,

        "wifi_mode": state.wifi_mode,
        "fs_readonly": state.fs_readonly,
        "ip": state.ip_str_cached,
        "mdns": (state.mdns_hostname + ".local") if (state.wifi_mode == config.WIFI_MODE_STA and state.mdns_hostname) else None,

        "cloud_enabled": state.cloud_enabled,
        "cloud_interval_sec": state.cloud_interval_sec,
        "cloud_configured": bool(state.cloud_api_url) and bool(state.cloud_device_token),
        "cloud_last_attempt_ts": state.cloud_last_attempt_ts,
        "cloud_last_http": state.cloud_last_http,
        "cloud_last_error": state.cloud_last_error,
        "rate_of_change": state.rate_of_change,
    }

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

    header, body = make_json_response(payload)
    send_all(conn, header)
    send_all(conn, body)


# ----------------------------------------------------------------------
# Calibration page + route
# ----------------------------------------------------------------------
def render_calibration_page(authed_pw=""):
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

    if authed_pw:
        _esc_pw = authed_pw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        calibration_pw_field = "<input type=\"hidden\" name=\"pw\" value=\"" + _esc_pw + "\">\n"
    else:
        calibration_pw_field = ""
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
    <form method='GET' action='/calibration'>
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


def handle_calibration_route(conn, params):
    """Apply altitude/pressure/ASC/forced-calibration changes (via the sensor
    driver) and render the calibration page. Write ops are admin-password
    protected when one is configured; the read view is always available."""
    s = state.settings
    sensor = state.sensor
    scd_available = (sensor is not None)

    _WRITE_PARAMS = {"reset", "calibrate", "altitude", "pressure", "asc", "update"}
    _has_write_op = params and any(k in params for k in _WRITE_PARAMS)
    _admin_pw = s.get("admin_password", "")
    if _has_write_op and _admin_pw:
        _provided = params.get("pw", "")
        if _provided != _admin_pw:
            _login_html = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Calibration - Login</title>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<style>body{font-family:sans-serif;background:#0b0b0b;color:#eee;margin:0}
.wrap{max-width:480px;margin:0 auto;padding:16px;text-align:center}
input{padding:6px;border-radius:4px;border:1px solid #444;background:#111;color:#eee;font-size:14px;width:80%;max-width:260px}
button{margin-top:16px;padding:8px 16px;border-radius:4px;border:1px solid #00bcd4;background:#00bcd4;color:#000;font-weight:600;cursor:pointer}
</style></head><body><div class='wrap'>
<h1>Know CO&#8322; Calibration</h1><p>Enter settings password to apply changes:</p>
<form method='GET' action='/calibration'>
<label>Password<br><input type='password' name='pw'></label><br>
<button type='submit'>Unlock</button></form>
<p><a href='/' style='color:#00bcd4;'>Back to settings</a></p>
</div></body></html>"""
            _header, _body = make_html_response(_login_html)
            send_all(conn, _header)
            send_all(conn, _body)
            return

    if params:
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
    html = render_calibration_page(authed_pw=params.get("pw", "") if params else "")
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)


# ----------------------------------------------------------------------
# ZIP package OTA helpers
# ----------------------------------------------------------------------
def _u16(b, o):
    """Little-endian uint16 from bytes at offset o."""
    return b[o] | (b[o + 1] << 8)


def _u32(b, o):
    """Little-endian uint32 from bytes at offset o."""
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def _zip_safe_path(name):
    """Return a cleaned, safe destination path for a ZIP entry, or None if unsafe."""
    name = name.replace("\\", "/")
    while name.startswith("/"):
        name = name[1:]
    if not name or name.endswith("/"):
        return None
    for part in name.split("/"):
        if part == ".." or part == ".":
            return None
    if name.startswith("__MACOSX/") or name.startswith("."):
        return None
    top = name.split("/")[0]
    if top in ("code.py", "boot.py"):
        return name if "/" not in name else None
    if top in ("lib", "assets", "knowco2"):
        return name
    return None


def _zip_ensure_dir(path):
    parts = path.split("/")
    current = ""
    for part in parts[:-1]:
        if not part:
            continue
        current = (current + "/" + part) if current else part
        try:
            import os as _e_os
            _e_os.mkdir(current)
        except OSError:
            pass  # already exists


def _parse_zip_entries(zip_path):
    """Parse a ZIP Central Directory; return (entries, None) or (None, error).
    Uses direct little-endian byte reads (no struct.unpack_from), which avoids
    a CircuitPython struct edge case seen on some builds."""
    try:
        with open(zip_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size < 22:
                return None, "File too small to be a ZIP"
            scan_size = min(file_size, 65558)
            f.seek(file_size - scan_size)
            tail = f.read(scan_size)
            eocd_rel = tail.rfind(b"PK\x05\x06")
            if eocd_rel < 0:
                return None, "Not a valid ZIP (no EOCD signature)"
            eocd = tail[eocd_rel: eocd_rel + 22]
            if len(eocd) < 22:
                return None, "Truncated EOCD record"
            # EOCD: sig(0) ... totalEntries(10,H) cdSize(12,I) cdOffset(16,I)
            if _u32(eocd, 0) != 0x06054b50:
                return None, "Bad EOCD signature"
            total_entries = _u16(eocd, 10)
            cd_size = _u32(eocd, 12)
            cd_offset = _u32(eocd, 16)
            if cd_size > 65536:
                return None, "Central Directory too large (>64 KB); ZIP64 not supported"
            f.seek(cd_offset)
            cd_data = f.read(cd_size)
            if len(cd_data) < cd_size:
                return None, "Truncated central directory"

        CD_SZ = 46
        entries = []
        pos = 0
        for _ in range(total_entries):
            if pos + CD_SZ > len(cd_data):
                break
            # CD entry: sig(0,I) method(10,H) compSize(20,I) uncompSize(24,I)
            #           nameLen(28,H) extraLen(30,H) commentLen(32,H) localOff(42,I)
            if _u32(cd_data, pos) != 0x02014b50:
                break
            method = _u16(cd_data, pos + 10)
            comp_size = _u32(cd_data, pos + 20)
            uncomp_size = _u32(cd_data, pos + 24)
            name_len = _u16(cd_data, pos + 28)
            extra_len = _u16(cd_data, pos + 30)
            comment_len = _u16(cd_data, pos + 32)
            local_off = _u32(cd_data, pos + 42)
            raw_name = cd_data[pos + CD_SZ: pos + CD_SZ + name_len]
            try:
                name = raw_name.decode("utf-8")
            except Exception:
                name = raw_name.decode("latin-1")
            pos += CD_SZ + name_len + extra_len + comment_len
            if name.endswith("/"):
                continue
            if method not in (0, 8):
                continue
            entries.append({
                "name": name,
                "local_off": local_off,
                "comp_size": comp_size,
                "uncomp_size": uncomp_size,
                "method": method,
            })
        return entries, None
    except Exception as e:
        return None, "ZIP parse error: " + str(e)


def _extract_zip_entry_to_file(zip_path, entry, dest_path):
    """Extract one STORED/DEFLATE entry to dest_path. Returns (ok, message).
    Local-header fields are read by direct byte indexing (no struct.unpack_from)."""
    import gc as _gc
    method = entry["method"]
    local_off = entry["local_off"]
    comp_size = entry["comp_size"]
    uncomp_size = entry["uncomp_size"]

    LFH_SZ = 30

    try:
        with open(zip_path, "rb") as zf:
            zf.seek(local_off)
            lhdr = zf.read(LFH_SZ)
            if len(lhdr) < LFH_SZ:
                return False, "Truncated local file header"
            # LFH: sig(0,I) ... nameLen(26,H) extraLen(28,H)
            if _u32(lhdr, 0) != 0x04034b50:
                return False, "Bad local file header signature"
            name_len = _u16(lhdr, 26)
            extra_len = _u16(lhdr, 28)
            # Seek past variable-length name and extra fields to reach data
            data_start = local_off + LFH_SZ + name_len + extra_len
            zf.seek(data_start)

            _zip_ensure_dir(dest_path)

            if method == 0:  # STORED
                written = 0
                with open(dest_path, "wb") as df:
                    while written < uncomp_size:
                        chunk = zf.read(min(512, uncomp_size - written))
                        if not chunk:
                            return False, "Premature end of STORED data at byte %d" % written
                        df.write(chunk)
                        written += len(chunk)
                        try:
                            if state._wd is not None:
                                state._wd.feed()
                        except Exception:
                            pass

            elif method == 8:  # DEFLATE
                import zlib as _zlib
                _gc.collect()
                compressed = zf.read(comp_size)
                if len(compressed) < comp_size:
                    return False, "Premature end of DEFLATE data"
                try:
                    decompressed = _zlib.decompress(compressed, -15)
                except Exception as ze:
                    return False, "Decompression failed: " + str(ze)
                del compressed
                _gc.collect()
                with open(dest_path, "wb") as df:
                    df.write(decompressed)
                del decompressed
                _gc.collect()

        return True, "OK"
    except Exception as e:
        return False, "Extract error: " + str(e)


def _process_zip_update(conn, zip_path):
    """Install a ZIP update: validate, extract allowed paths, swap code.py, reboot."""
    import os as _oz
    import gc as _gcz

    log("ota", "Parsing ZIP package...")
    entries, err = _parse_zip_entries(zip_path)
    if entries is None:
        try: _oz.remove(zip_path)
        except Exception: pass
        _send_ota_result(conn, False, "ZIP parse failed: " + (err or "unknown"))
        return

    if not entries:
        try: _oz.remove(zip_path)
        except Exception: pass
        _send_ota_result(conn, False, "ZIP is empty - no files found.")
        return

    safe = []
    skipped = []
    for e in entries:
        dest = _zip_safe_path(e["name"])
        if dest:
            safe.append((dest, e))
        else:
            skipped.append(e["name"])

    if not safe:
        try: _oz.remove(zip_path)
        except Exception: pass
        _send_ota_result(conn, False,
            "ZIP contains no installable files. "
            "Allowed top-level names: code.py, boot.py, lib/, assets/, knowco2/. "
            "Found: " + ", ".join(e["name"] for e in entries[:8]))
        return

    try:
        if state._wd is not None:
            state._wd.timeout = 90
    except Exception:
        pass

    code_tmp = "/code.py.ota"
    has_code = any(dest == "code.py" for dest, _ in safe)
    if has_code:
        code_entry = next(e for d, e in safe if d == "code.py")
        ok, msg = _extract_zip_entry_to_file(zip_path, code_entry, code_tmp)
        if not ok:
            try: _oz.remove(zip_path)
            except Exception: pass
            try: _oz.remove(code_tmp)
            except Exception: pass
            _send_ota_result(conn, False, "Failed to extract code.py: " + msg)
            return
        try:
            with open(code_tmp, "rb") as _f:
                _head = _f.read(64).lstrip(b"\xef\xbb\xbf")
            _valid = (_head.startswith(b"#") or _head.startswith(b"import ") or
                      _head.startswith(b"from ") or _head.startswith(b"\n#") or
                      _head.startswith(b"\r\n#"))
            if not _valid:
                try: _oz.remove(zip_path)
                except Exception: pass
                try: _oz.remove(code_tmp)
                except Exception: pass
                _send_ota_result(conn, False,
                    "code.py in ZIP does not look like Python source "
                    "(first bytes: %r). Aborting - nothing was changed." % _head[:16])
                return
        except Exception as ce:
            _send_ota_result(conn, False, "Cannot verify code.py: " + str(ce))
            return

    installed = []
    errors = []
    for dest, e in safe:
        if dest == "code.py":
            continue
        ok, msg = _extract_zip_entry_to_file(zip_path, e, dest)
        if ok:
            installed.append(dest)
        else:
            errors.append("%s: %s" % (dest, msg))
        _gcz.collect()

    if has_code:
        try:
            try: _oz.remove("/code.py.bak")
            except Exception: pass
            try: _oz.rename("/code.py", "/code.py.bak")
            except Exception: pass
            _oz.rename(code_tmp, "/code.py")
            installed.append("code.py")
        except Exception as re_err:
            _send_ota_result(conn, False, "Failed to install code.py: " + str(re_err))
            return

    try: _oz.remove(zip_path)
    except Exception: pass

    summary = "Installed (%d files): %s." % (len(installed), ", ".join(installed))
    if errors:
        summary += " Warnings: " + "; ".join(errors) + "."
    if skipped:
        summary += " Skipped (outside allowed paths): " + ", ".join(skipped[:4]) + "."

    _send_ota_result(conn, True, summary + " Rebooting in 3 seconds.")
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
    """OTA firmware update. GET shows the form; POST installs a .py or .zip."""
    s = state.settings
    admin_pw = s.get("admin_password", "")
    if admin_pw:
        provided_pw = params.get("pw", "")
        if provided_pw != admin_pw:
            login_html = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>OTA Update - Login</title>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<style>body{font-family:sans-serif;background:#0b0b0b;color:#eee;margin:0}
.wrap{max-width:480px;margin:0 auto;padding:16px;text-align:center}
input{padding:6px;border-radius:4px;border:1px solid #444;background:#111;color:#eee;font-size:14px;width:80%;max-width:260px}
button{margin-top:16px;padding:8px 16px;border-radius:4px;border:1px solid #00bcd4;background:#00bcd4;color:#000;font-weight:600;cursor:pointer}
</style></head><body><div class='wrap'>
<h1>Know CO&#8322; OTA</h1><p>Enter settings password:</p>
<form method='POST' action='/update'>
<label>Password<br><input type='password' name='pw'></label><br>
<button type='submit'>Unlock</button></form></div></body></html>"""
            header, body = make_html_response(login_html)
            send_all(conn, header)
            send_all(conn, body)
            return

    # --- FILE UPLOAD: POST with ?upload=1, raw binary body ---
    if method == b"POST" and params.get("upload") == "1":
        try:
            with open(SETTINGS_FILE, "rb") as _sf:
                _sdata = _sf.read()
            with open(SETTINGS_FILE + ".bak", "wb") as _bf:
                _bf.write(_sdata)
        except Exception:
            pass
        try:
            import os as _pre_os
            try: _pre_os.remove("/update.tmp")
            except Exception: pass
            try: _pre_os.remove("/code.py.bak")
            except Exception: pass
        except Exception:
            pass
        tmp_path = "/update.tmp"
        ok, msg = stream_request_body_to_file(conn, raw_headers, tmp_path)
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

        _head_str = _magic + b""
        try:
            with open(tmp_path, "rb") as _f:
                _head_str = (_magic + _f.read(60)).lstrip(b"\xef\xbb\xbf")
        except Exception:
            pass
        _valid = (
            _head_str.startswith(b"#") or
            _head_str.startswith(b"import ") or
            _head_str.startswith(b"from ") or
            _head_str.startswith(b"\n#") or
            _head_str.startswith(b"\r\n#")
        )
        if not _valid:
            import os as _os2
            _os2.remove(tmp_path)
            _send_ota_result(conn, False,
                "Upload rejected: file is not a ZIP package and does not look like "
                "Python source (first bytes: %r). Aborting - nothing changed." % _head_str[:16])
            return
        try:
            import os as _os
            try: _os.remove("/code.py.bak")
            except Exception: pass
            try: _os.rename("/code.py", "/code.py.bak")
            except Exception: pass
            _os.rename(tmp_path, "/code.py")
        except Exception as e:
            _send_ota_result(conn, False, "File rename failed: " + str(e))
            return
        _send_ota_result(conn, True, "Firmware uploaded and written to /code.py. Rebooting in 3 seconds.")
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
        return

    # --- URL DOWNLOAD: POST with firmware_url ---
    if method == b"POST" and "firmware_url" in params:
        fw_url = params.get("firmware_url", "").strip()
        if not fw_url:
            _send_ota_result(conn, False, "No URL provided.")
            return
        if state.wifi_mode != config.WIFI_MODE_STA or wifi is None or not wifi.radio.connected:
            _send_ota_result(conn, False, "Must be in STA (WiFi) mode to download firmware.")
            return
        try:
            with open(SETTINGS_FILE, "rb") as _sf:
                _sdata = _sf.read()
            with open(SETTINGS_FILE + ".bak", "wb") as _bf:
                _bf.write(_sdata)
        except Exception:
            pass
        try:
            import ssl as _ssl
            import adafruit_requests as _requests
            pool = socketpool.SocketPool(wifi.radio)
            if fw_url.startswith("https"):
                _ssl_ctx = _ssl.create_default_context()
                session = _requests.Session(pool, _ssl_ctx)
            else:
                session = _requests.Session(pool)
            response = session.get(fw_url, timeout=30)
            if response.status_code != 200:
                _send_ota_result(conn, False, "HTTP %d fetching firmware." % response.status_code)
                return
            tmp_path = "/code.py.ota"
            try:
                if state._wd is not None:
                    state._wd.timeout = 90
            except Exception:
                pass
            with open(tmp_path, "wb") as f:
                try:
                    for chunk in response.iter_content(chunk_size=512):
                        if chunk:
                            f.write(chunk)
                            try:
                                if state._wd is not None:
                                    state._wd.feed()
                            except Exception:
                                pass
                except AttributeError:
                    f.write(response.content)
            response.close()
            try:
                with open(tmp_path, "rb") as _f:
                    _head = _f.read(64)
                _head_str = _head.lstrip(b"\xef\xbb\xbf")
                _valid = (
                    _head_str.startswith(b"#") or
                    _head_str.startswith(b"import ") or
                    _head_str.startswith(b"from ") or
                    _head_str.startswith(b"\n#") or
                    _head_str.startswith(b"\r\n#")
                )
                if not _valid:
                    import os as _os2
                    _os2.remove(tmp_path)
                    _send_ota_result(conn, False,
                        "Download rejected: file does not appear to be valid Python "
                        "(first bytes: %r). Existing firmware unchanged." % _head[:16])
                    return
            except Exception as check_err:
                _send_ota_result(conn, False, "Could not verify downloaded file: " + str(check_err))
                return
            try:
                import os as _os
                try: _os.remove("/code.py.bak")
                except Exception: pass
                try: _os.rename("/code.py", "/code.py.bak")
                except Exception: pass
                _os.rename(tmp_path, "/code.py")
            except Exception as rename_err:
                _send_ota_result(conn, False, "File rename failed: " + str(rename_err))
                return
            _send_ota_result(conn, True, "Firmware downloaded and written to /code.py. Rebooting in 3 seconds.")
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
        except Exception as ota_err:
            _send_ota_result(conn, False, "OTA error: " + str(ota_err))
        return

    # --- GET: show the combined update form ---
    pw_val = params.get("pw", "")
    pw_field = ("<input type='hidden' name='pw' value='%s'>" % pw_val) if pw_val else ""
    form_html = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Know CO2 - OTA Update</title>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0b0b0b;color:#eee;margin:0}
.wrap{max-width:640px;margin:0 auto;padding:16px}
h1{color:#00bcd4;margin-top:8px}
h2{font-size:16px;color:#aaa;margin:20px 0 4px}
label{display:block;margin-top:10px;font-size:14px}
input[type=text],input[type=url]{width:100%;box-sizing:border-box;padding:8px;border-radius:4px;border:1px solid #444;background:#111;color:#eee;font-size:14px}
.drop-zone{border:2px dashed #444;border-radius:8px;padding:24px;text-align:center;cursor:pointer;transition:border-color .2s;margin-top:8px}
.drop-zone.dragover,.drop-zone:hover{border-color:#00bcd4}
.drop-zone input[type=file]{display:none}
.drop-zone p{margin:4px 0;color:#aaa;font-size:14px}
.drop-zone .sub{font-size:12px;color:#666;margin-top:4px}
#upload-btn,#url-btn{margin-top:12px;padding:8px 20px;border-radius:4px;border:1px solid #e53935;background:#e53935;color:#fff;font-weight:600;cursor:pointer;font-size:14px;display:inline-block}
#upload-btn:disabled,#url-btn:disabled{opacity:.5;cursor:not-allowed}
.warn{color:#ffb300;font-size:13px;margin-top:8px}
.info{background:#111;border:1px solid #333;border-radius:6px;padding:12px 14px;margin-top:10px;font-size:13px;color:#bbb;line-height:1.6}
.info code{background:#1e1e1e;padding:1px 5px;border-radius:3px;font-size:12px;color:#80cbc4}
.info pre{background:#1e1e1e;padding:10px;border-radius:4px;overflow-x:auto;font-size:12px;color:#80cbc4;margin:6px 0 0}
.progress{display:none;margin-top:8px;font-size:13px;color:#aaa}
a{color:#00bcd4}
hr{border:none;border-top:1px solid #333;margin:20px 0}
</style></head><body><div class='wrap'>
<h1>OTA Update</h1>
<p class='warn'>&#9888; Settings are backed up automatically before every update and restored on reboot if needed.</p>

<h2>Option 1 &mdash; Upload from your computer</h2>
<div class='drop-zone' id='drop-zone' onclick="document.getElementById('fw-file-input').click()">
  <p>&#128230; Drag &amp; drop a <code>.zip</code> package <em>or</em> a <code>.py</code> firmware file</p>
  <p class='sub'>ZIP updates firmware + libraries + boot.py + assets &nbsp;|&nbsp; .py updates firmware only</p>
  <input type='file' id='fw-file-input' accept='.py,.zip,.txt'>
</div>
<div class='progress' id='upload-progress'>Uploading...</div>
<button id='upload-btn' disabled onclick='doUpload()'>Upload &amp; Install</button>

<div class='info'>
<strong>Building a full-update ZIP package</strong><br>
Include any combination of these top-level paths:<br>
<code>code.py</code> &nbsp; <code>boot.py</code> &nbsp; <code>lib/</code> &nbsp; <code>assets/</code> &nbsp; <code>knowco2/</code><br><br>
macOS &amp; Linux:
<pre>zip -r knowco2-update.zip code.py boot.py knowco2/ lib/ assets/</pre>
Any file outside those paths is safely ignored.<br>
<strong>Your settings (Wi-Fi, thresholds, etc.) are never touched by an update.</strong>
</div>

<hr>
<h2>Option 2 &mdash; Download from a URL (STA mode only)</h2>
<form method='POST' action='/update'>""" + pw_field + """
<label>Firmware or package URL<br><input type='url' name='firmware_url' placeholder='http://192.168.1.x/firmware.py'></label>
<button id='url-btn' type='submit'>Download &amp; Install</button>
</form>

<p style='margin-top:20px'><a href='/'>&#8592; Back to Settings</a></p>
</div>
<script>
var _file = null;
var _pw = '""" + pw_val + """';
var dz = document.getElementById('drop-zone');
var fi = document.getElementById('fw-file-input');
var ub = document.getElementById('upload-btn');
var pr = document.getElementById('upload-progress');

function _label(f){
  var ext = f.name.split('.').pop().toLowerCase();
  var type = ext === 'zip' ? 'package' : 'firmware';
  return '\\u2713 ' + f.name + ' (' + Math.round(f.size/1024) + ' KB) \u2014 ' + type + ' update';
}
fi.addEventListener('change', function(){
  if (fi.files.length) { _file = fi.files[0]; ub.disabled = false; dz.querySelector('p').textContent = _label(_file); }
});
dz.addEventListener('dragover', function(e){ e.preventDefault(); dz.classList.add('dragover'); });
dz.addEventListener('dragleave', function(){ dz.classList.remove('dragover'); });
dz.addEventListener('drop', function(e){
  e.preventDefault(); dz.classList.remove('dragover');
  if (e.dataTransfer.files.length){ _file = e.dataTransfer.files[0]; ub.disabled = false; dz.querySelector('p').textContent = _label(_file); }
});

function doUpload(){
  if (!_file) return;
  var ext = _file.name.split('.').pop().toLowerCase();
  var kind = ext === 'zip' ? 'full update package' : 'firmware file';
  if (!confirm('Install ' + _file.name + ' as a ' + kind + '? The device will reboot.')) return;
  ub.disabled = true;
  pr.style.display = 'block';
  pr.textContent = 'Uploading ' + _file.name + '...';
  var url = '/update?upload=1' + (_pw ? '&pw=' + encodeURIComponent(_pw) : '');
  var xhr = new XMLHttpRequest();
  xhr.open('POST', url, true);
  xhr.setRequestHeader('Content-Type', 'application/octet-stream');
  xhr.onload = function(){ pr.innerHTML = xhr.responseText; };
  xhr.onerror = function(){ pr.textContent = 'Upload failed (network error)'; ub.disabled = false; };
  xhr.upload.onprogress = function(e){
    if (e.lengthComputable) {
      var pct = Math.round(e.loaded/e.total*100);
      if (pct < 100) pr.textContent = 'Uploading... ' + pct + '%';
    }
  };
  xhr.upload.onload = function(){
    pr.textContent = 'Writing firmware\u2026 do not disconnect';
  };
  xhr.send(_file);
}
</script>
</body></html>"""
    header, body = make_html_response(form_html)
    send_all(conn, header)
    send_all(conn, body)


def _send_ota_result(conn, success, message):
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


def handle_root_route(conn, params):
    """Settings page (and POST handler). Admin-password gated when configured."""
    s = state.settings
    try:
        admin_pw = s.get("admin_password", "")
    except Exception:
        admin_pw = ""
    if admin_pw:
        provided_pw = params.get("pw")
        if not provided_pw or provided_pw != admin_pw:
            login_html = """<!DOCTYPE html>
<html>
<head>
  <meta charset='utf-8'>
  <title>Know CO2 Settings Login</title>
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
    if settings_params and len(settings_params) > 0:
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

    try:
        data = read_request_head(conn)
        if not data:
            try: conn.close()
            except Exception: pass
            return

        first_line = data.split(b"\r\n", 1)[0]
        log("req", "HTTP", addr, first_line, min_interval=0.2)

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

        if method not in (b"GET", b"HEAD", b"POST"):
            header, body = build_response(405, "text/plain; charset=utf-8", b"Method Not Allowed")
            send_all(conn, header)
            send_all(conn, body)
            return

        route, params = parse_query(path)

        _is_ota_upload = (route == "/update" and "upload" in params)
        if method == b"POST" and not _is_ota_upload:
            post_body = read_request_body(conn, data)
            if post_body:
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

        if route == "/data":
            handle_data_route(conn)
        elif route == "/status":
            handle_status_route(conn)
        elif route == "/export.csv":
            handle_export_csv_route(conn)
        elif route == "/calibration":
            handle_calibration_route(conn, params)
        elif route == "/update":
            handle_update_route(conn, params, method=method, raw_headers=data)
        else:
            handle_root_route(conn, params)

    except Exception as e:
        log("http_err", "HTTP error:", e, min_interval=1.0)
    finally:
        try:
            conn.close()
        except Exception:
            pass
