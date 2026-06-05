# knowco2/settings.py
# ----------------------------------------------------------------------
# Settings persistence and application.
#   * load_settings()  — read settings.json (with .bak fallback/restore)
#   * apply_settings() — push values into live runtime state
#   * save_settings()  — atomic write + backup
#   * ensure_ap_credentials() — generate AP SSID/password if missing
#
# Live values that change at runtime live in state.py; this module is what
# moves them between the JSON file and state.
# ----------------------------------------------------------------------

import json
import os

import storage

from . import state, config, runtime
from .helpers import log, as_int, clamp_int, rand_token, rand_safe32

SETTINGS_FILE = config.SETTINGS_FILE

DEFAULT_SETTINGS = {
    "low_threshold": config.LOW_THRESHOLD_DEFAULT,
    "med_threshold": config.MED_THRESHOLD_DEFAULT,
    "alert_threshold": config.ALERT_THRESHOLD_DEFAULT,
    "alerts_enabled": True,
    "graph_scale_mode": "fixed",
    "max_points": config.MAX_POINTS_DEFAULT,

    "ap_ssid": "",
    "ap_password": "",

    "sta_ssid": "",
    "sta_password": "",

    "device_id": "co2-node-1",
    "temp_mode": "F",
    "display_mode": 0,
    "display_flip": False,

    # Cloud telemetry
    "cloud_enabled": False,
    "cloud_api_url": "",
    "cloud_device_token": "",
    "cloud_interval_sec": 60,

    # ---- MQTT broker (e.g. Home Assistant) ----
    # mqtt_enabled: publish CO2/temp/RH to a standard MQTT broker.
    "mqtt_enabled": False,
    "mqtt_broker": "",           # hostname or IP
    "mqtt_port": 1883,
    "mqtt_user": "",
    "mqtt_pass": "",
    "mqtt_topic_prefix": "knowco2",  # topics: <prefix>/co2, /temp_c, /rh
    "mqtt_interval_sec": 60,

    # ---- Adafruit IO ----
    "aio_enabled": False,
    "aio_username": "",
    "aio_key": "",
    "aio_group_key": "knowco2",  # feed names: <group>.co2, .temperature, .humidity
    "aio_interval_sec": 60,

    # ---- Display dimming schedule ----
    # dim_enabled: automatically dim the display between dim_start_hour and dim_end_hour.
    # Requires NTP sync.  dim_brightness is 0-100 (percent of full brightness).
    "dim_enabled": False,
    "dim_start_hour": 22,   # 10 PM
    "dim_end_hour": 7,      # 7 AM
    "dim_brightness": 10,   # 10% during dim hours

    # Optional admin password to protect the settings page.  If this string is
    # non-empty, the root settings page will require a matching "pw" query
    # parameter.  Use the web UI below to set or clear this password.
    "admin_password": "",

    # UI language for the web settings page.  Applied client-side via JavaScript.
    # Options: en, es, fr, de, pt, it, ja, zh
    # Device display is always English (font supports ASCII only).
    "lang": "en",
    "energy_mode": False,
    "colorblind_mode": False,  # use Wong colorblind-safe palette (blue/amber/vermillion)

    # Calibration settings (CO₂ sensor)
    # When asc_enabled is True, the SCD4x will use its built‑in Automatic Self Calibration
    # algorithm.  Set altitude (in meters above sea level) or ambient_pressure (in hPa)
    # to improve accuracy.  A value of 0 for altitude or pressure leaves that
    # compensation disabled.  last_calibration_ts and last_calibration_ref record the
    # timestamp and reference value of the most recent forced calibration.
    "asc_enabled": True,
    "altitude": 0,
    "ambient_pressure": 0,
    "last_calibration_ts": 0,
    "last_calibration_ref": 0,
}


def ensure_fs_writable():
    """Try to make CIRCUITPY writable (works when a USB host has not mounted
    it as mass storage). Updates state.fs_readonly."""
    try:
        storage.remount("/", readonly=False)
    except Exception:
        pass
    try:
        state.fs_readonly = bool(storage.getmount("/").readonly)
    except Exception:
        pass


def apply_settings():
    s = state.settings
    state.LOW_THRESHOLD = int(s.get("low_threshold", config.LOW_THRESHOLD_DEFAULT))
    state.MED_THRESHOLD = int(s.get("med_threshold", config.MED_THRESHOLD_DEFAULT))
    state.ALERT_THRESHOLD = int(s.get("alert_threshold", config.ALERT_THRESHOLD_DEFAULT))

    state.alerts_enabled = bool(s.get("alerts_enabled", True))
    state.graph_scale_mode = s.get("graph_scale_mode", "fixed")

    state.MAX_POINTS = clamp_int(s.get("max_points", config.MAX_POINTS_DEFAULT),
                                 100, 50000, config.MAX_POINTS_DEFAULT)

    state.cloud_enabled = bool(s.get("cloud_enabled", False))
    state.cloud_api_url = (s.get("cloud_api_url", "") or "").strip()
    state.cloud_device_token = (s.get("cloud_device_token", "") or "").strip()
    state.cloud_interval_sec = clamp_int(s.get("cloud_interval_sec", 60) or 60, 15, 3600, 60)

    # Apply display orientation immediately (no reboot needed).
    try:
        import board as _b
        _b.DISPLAY.rotation = 0 if s.get("display_flip", False) else 180
    except Exception:
        pass

    # Colour scheme is a UI concern — go through the registered hook.
    try:
        runtime.apply_color_scheme()
    except Exception:
        pass


def load_settings():
    s = state.settings
    # Seed any missing keys with defaults (first boot or partial file).
    for _k, _v in DEFAULT_SETTINGS.items():
        s.setdefault(_k, _v)
    loaded = False
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            s.update(data)
            loaded = True
    except OSError:
        pass
    except ValueError:
        pass

    if not loaded:
        # Primary file missing/corrupt — try the backup written on every save.
        _bak = SETTINGS_FILE + ".bak"
        try:
            with open(_bak, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                s.update(data)
                loaded = True
                try:
                    ensure_fs_writable()
                    with open(SETTINGS_FILE, "w") as f:
                        json.dump(s, f)
                except Exception:
                    pass
        except OSError:
            pass
        except ValueError:
            pass

    apply_settings()

    tm = s.get("temp_mode", "F")
    if tm in ("F", "C"):
        state.temp_mode = tm

    dm_int = as_int(s.get("display_mode", 0), 0)
    if dm_int not in (0, 1, 2):
        dm_int = 0
    state.display_mode = dm_int


def save_settings():
    ensure_fs_writable()
    if state.fs_readonly:
        log("save_settings", "settings not saved (filesystem is read-only)", min_interval=10.0)
        return False

    s = state.settings
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(s, f)
        try:
            os.replace(tmp, SETTINGS_FILE)
        except AttributeError:
            os.rename(tmp, SETTINGS_FILE)
        try:
            with open(SETTINGS_FILE + ".bak", "w") as _bk:
                json.dump(s, _bk)
        except Exception:
            pass
        return True
    except OSError as e:
        try:
            if (e.args and e.args[0] == 30) or getattr(e, "errno", None) == 30:
                state.fs_readonly = True
        except Exception:
            pass

        ensure_fs_writable()
        if not state.fs_readonly:
            try:
                with open(tmp, "w") as f:
                    json.dump(s, f)
                try:
                    os.replace(tmp, SETTINGS_FILE)
                except AttributeError:
                    os.rename(tmp, SETTINGS_FILE)
                try:
                    with open(SETTINGS_FILE + ".bak", "w") as _bk:
                        json.dump(s, _bk)
                except Exception:
                    pass
                return True
            except Exception:
                pass

        log("save_settings", "Error saving settings.json:", e, min_interval=10.0)
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def generate_ap_credentials():
    suffix = rand_token(2)
    ssid = "knowco2-" + suffix
    pw = rand_safe32(8)
    return ssid, pw


def ensure_ap_credentials():
    s = state.settings
    ssid = (s.get("ap_ssid", "") or "").strip()
    pw = (s.get("ap_password", "") or "").strip()
    if len(ssid) < 1 or len(pw) < 8:
        new_ssid, new_pw = generate_ap_credentials()
        s["ap_ssid"] = new_ssid
        s["ap_password"] = new_pw
        save_settings()
        print("Generated AP creds:", new_ssid, new_pw)


def update_settings_from_params(params):
    """Apply form/query params from the web portal into settings, persist, and
    re-apply. Returns True if the AP SSID/password changed (caller restarts AP).
    Colour scheme is re-applied through the UI hook."""
    s = state.settings
    ap_changed = False

    old_ap_ssid = s.get("ap_ssid", "")
    old_ap_pass = s.get("ap_password", "")

    if "regen_ap" in params:
        new_ssid, new_pw = generate_ap_credentials()
        s["ap_ssid"] = new_ssid
        s["ap_password"] = new_pw
        ap_changed = True

    if "low" in params:
        try: s["low_threshold"] = int(params["low"])
        except ValueError: pass
    if "med" in params:
        try: s["med_threshold"] = int(params["med"])
        except ValueError: pass
    if "alert" in params:
        try: s["alert_threshold"] = int(params["alert"])
        except ValueError: pass

    if "max_points" in params:
        try: s["max_points"] = int(params["max_points"])
        except ValueError: pass

    if "scale" in params and params["scale"] in ("fixed", "wide", "auto"):
        s["graph_scale_mode"] = params["scale"]

    s["alerts_enabled"] = "alerts" in params

    if "device_id" in params and params["device_id"]:
        s["device_id"] = params["device_id"]

    if "admin_pw" in params:
        s["admin_password"] = params["admin_pw"] or ""

    if "lang" in params and params["lang"] in ("en", "es", "fr", "de", "pt", "it", "ja", "zh", "ko"):
        s["lang"] = params["lang"]

    if "ap_ssid" in params and params["ap_ssid"]:
        new_ssid = params["ap_ssid"]
        if new_ssid != old_ap_ssid:
            s["ap_ssid"] = new_ssid
            ap_changed = True

    if "ap_password" in params and params["ap_password"]:
        new_pass = params["ap_password"]
        if len(new_pass) >= 8 and new_pass != old_ap_pass:
            s["ap_password"] = new_pass
            ap_changed = True

    if "sta_ssid" in params:
        s["sta_ssid"] = params["sta_ssid"]
    if "sta_password" in params and params["sta_password"]:
        s["sta_password"] = params["sta_password"]

    if "temp_mode" in params:
        tm = params["temp_mode"]
        if tm in ("F", "C"):
            state.temp_mode = tm
            s["temp_mode"] = tm

    if "mode" in params:
        try: dm = int(params["mode"])
        except ValueError: dm = state.display_mode
        if dm in (0, 1, 2):
            state.display_mode = dm
            s["display_mode"] = dm

    s["display_flip"] = "display_flip" in params
    s["colorblind_mode"] = "colorblind_mode" in params
    runtime.apply_color_scheme()

    # Cloud settings
    s["cloud_enabled"] = "cloud_enabled" in params
    if "cloud_api_url" in params:
        s["cloud_api_url"] = params["cloud_api_url"]
    if "cloud_device_token" in params and params["cloud_device_token"]:
        s["cloud_device_token"] = params["cloud_device_token"]
    elif "cloud_device_secret" in params and params["cloud_device_secret"]:
        s["cloud_device_token"] = params["cloud_device_secret"]
    elif "cloud_token" in params and params["cloud_token"]:
        s["cloud_device_token"] = params["cloud_token"]
    if "cloud_interval_sec" in params:
        try: s["cloud_interval_sec"] = int(params["cloud_interval_sec"])
        except Exception: pass

    # MQTT broker settings
    s["mqtt_enabled"] = "mqtt_enabled" in params
    if "mqtt_broker" in params:
        s["mqtt_broker"] = params["mqtt_broker"].strip()
    if "mqtt_port" in params:
        try: s["mqtt_port"] = int(params["mqtt_port"])
        except Exception: pass
    if "mqtt_user" in params:
        s["mqtt_user"] = params["mqtt_user"]
    if "mqtt_pass" in params and params["mqtt_pass"]:
        s["mqtt_pass"] = params["mqtt_pass"]
    if "mqtt_topic_prefix" in params and params["mqtt_topic_prefix"]:
        s["mqtt_topic_prefix"] = params["mqtt_topic_prefix"].strip()
    if "mqtt_interval_sec" in params:
        try: s["mqtt_interval_sec"] = max(15, int(params["mqtt_interval_sec"]))
        except Exception: pass

    # Adafruit IO settings
    s["aio_enabled"] = "aio_enabled" in params
    if "aio_username" in params:
        s["aio_username"] = params["aio_username"].strip()
    if "aio_key" in params and params["aio_key"]:
        s["aio_key"] = params["aio_key"]
    if "aio_group_key" in params and params["aio_group_key"]:
        s["aio_group_key"] = params["aio_group_key"].strip()
    if "aio_interval_sec" in params:
        try: s["aio_interval_sec"] = max(15, int(params["aio_interval_sec"]))
        except Exception: pass

    # Display dimming schedule
    s["dim_enabled"] = "dim_enabled" in params
    if "dim_start_hour" in params:
        try: s["dim_start_hour"] = max(0, min(23, int(params["dim_start_hour"])))
        except Exception: pass
    if "dim_end_hour" in params:
        try: s["dim_end_hour"] = max(0, min(23, int(params["dim_end_hour"])))
        except Exception: pass
    if "dim_brightness" in params:
        try: s["dim_brightness"] = max(0, min(100, int(params["dim_brightness"])))
        except Exception: pass

    # Validate + reorder thresholds: clamp to [400, 10000], enforce low<=med<=alert.
    try:
        low = int(s.get("low_threshold", config.LOW_THRESHOLD_DEFAULT))
        med = int(s.get("med_threshold", config.MED_THRESHOLD_DEFAULT))
        alert = int(s.get("alert_threshold", config.ALERT_THRESHOLD_DEFAULT))
        vals = [max(400, min(10000, v)) for v in (low, med, alert)]
        vals.sort()
        s["low_threshold"], s["med_threshold"], s["alert_threshold"] = vals
    except Exception:
        pass

    save_settings()
    apply_settings()
    return ap_changed
