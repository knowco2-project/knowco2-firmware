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

from . import state, config, runtime, crypto
from .helpers import log, as_int, clamp_int, rand_token, rand_safe32
from .languages import SUPPORTED_LANGUAGE_CODES

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
    "sta_ssid_2": "",
    "sta_password_2": "",
    "sta_ssid_3": "",
    "sta_password_3": "",

    "device_id": "co2-node-1",
    "temp_mode": "F",
    "display_mode": 0,
    "display_flip": False,

    # Cloud telemetry
    "cloud_enabled": False,
    "cloud_api_url": "",
    "cloud_device_id": "",
    "cloud_device_token": "",
    "cloud_interval_sec": 60,

    # ---- MQTT broker (e.g. Home Assistant) ----
    # mqtt_enabled: publish CO2/temp/RH to a standard MQTT broker.
    "mqtt_enabled": False,
    "mqtt_broker": "",           # hostname or IP
    "mqtt_port": 8883,
    # Secure by default. Advanced anonymous-only local brokers may opt out by
    # editing settings directly; credentialed plaintext MQTT is always refused.
    "mqtt_use_tls": True,
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

    # UI language for the web settings page. Applied client-side in a browser.
    # The catalog lives in languages.py; the device display remains English.
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
    """Try to make the device filesystem writable when a USB host has not
    mounted the service volume. Updates state.fs_readonly."""
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
    state.cloud_device_id = (s.get("cloud_device_id", "") or "").strip()
    state.cloud_device_token = (s.get("cloud_device_token", "") or "").strip()
    state.cloud_interval_sec = clamp_int(s.get("cloud_interval_sec", 60) or 60, 15, 3600, 60)
    if state.pending_cloud_claim:
        state.cloud_activation_state = "pending"
    elif state.cloud_device_token:
        state.cloud_activation_state = "configured"
    elif state.cloud_activation_state not in ("error",):
        state.cloud_activation_state = "unconfigured"

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

    # Legacy firmware stored the admin password in plaintext. Replace it
    # with the salted hash the first time this firmware loads it.
    _admin = s.get("admin_password", "") or ""
    if _admin and not crypto.is_hashed_admin_password(_admin):
        _hashed = crypto.hash_admin_password(str(_admin))
        if crypto.is_hashed_admin_password(_hashed):
            s["admin_password"] = _hashed
            if loaded:
                save_settings()

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
    pw = rand_safe32(config.AP_PASSWORD_LENGTH)
    return ssid, pw


# SHA-256 of setup-AP passwords that were published by mistake (a
# settings.json committed to the public firmware repository). A device that
# still carries one gets fresh credentials at boot.
_BURNED_AP_PASSWORD_SHA256 = (
    "b287c9aa154a196bc4fd3a5efcbeafc01a50bcce52621a994832453395abaf36",
)
_burn_cache = [None, False]  # [last password checked, result]


def ap_password_is_burned(pw):
    """True when *pw* is a setup-AP password known to be public."""
    if not pw:
        return False
    if _burn_cache[0] == pw:
        return _burn_cache[1]
    result = False
    try:
        import binascii
        digest = crypto.sha256_bytes(pw.encode("utf-8"))
        if digest is not None:
            result = binascii.hexlify(digest).decode("ascii") in _BURNED_AP_PASSWORD_SHA256
    except Exception:
        result = False
    _burn_cache[0] = pw
    _burn_cache[1] = result
    return result


def ensure_ap_credentials():
    s = state.settings
    ssid = (s.get("ap_ssid", "") or "").strip()
    pw = (s.get("ap_password", "") or "").strip()
    if len(ssid) < 1 or len(pw) < 8 or ap_password_is_burned(pw):
        new_ssid, new_pw = generate_ap_credentials()
        s["ap_ssid"] = new_ssid
        s["ap_password"] = new_pw
        save_settings()
        # Never print the password: the console is reachable in maintenance mode.
        print("Generated new setup-AP credentials for", new_ssid)


def factory_reset():
    """Erase every stored setting and secret (Wi-Fi, cloud pairing, MQTT /
    Adafruit IO credentials, admin password, calibration notes). The caller
    reboots afterwards; the next boot starts from defaults and generates new
    setup-AP credentials. Returns False when the filesystem is read-only."""
    ensure_fs_writable()
    if state.fs_readonly:
        return False
    for suffix in ("", ".bak", ".tmp"):
        try:
            os.remove(SETTINGS_FILE + suffix)
        except OSError:
            pass
    for suffix in ("", ".bak"):
        try:
            os.stat(SETTINGS_FILE + suffix)
            return False  # still there: do not report a wipe that did not happen
        except OSError:
            pass
    s = state.settings
    for key in list(s.keys()):
        del s[key]
    for key, value in DEFAULT_SETTINGS.items():
        s[key] = value
    state.pending_cloud_claim = None
    state.cloud_activation_request_id = None
    state.cloud_activation_state = "unconfigured"
    apply_settings()
    return True


def update_settings_from_params(params):
    """Apply form/query params from the web portal into settings, persist, and
    re-apply. Returns True if the AP SSID/password changed (caller restarts AP).
    Colour scheme is re-applied through the UI hook."""
    s = state.settings

    # Extra portal languages are loaded on demand. Their selector submits a
    # deliberately minimal form so the translated page can be re-rendered.
    # Keep this as an early, allow-listed path: the normal settings handler
    # interprets an absent checkbox as "off", so letting a locale-only request
    # fall through would disable unrelated features. Ignore every other field
    # even if a malformed client includes one.
    if params.get("lang_only") == "1":
        lang = params.get("lang")
        if lang in SUPPORTED_LANGUAGE_CODES:
            s["lang"] = lang
            save_settings()
        return False

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

    # A blank password field means "leave unchanged". Clearing protection must
    # be a separate, explicit, physically authorized operation; otherwise an
    # ordinary settings save silently disables authentication.
    if "admin_pw" in params and params["admin_pw"]:
        s["admin_password"] = crypto.hash_admin_password(params["admin_pw"])

    if "lang" in params and params["lang"] in SUPPORTED_LANGUAGE_CODES:
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
    if "sta_ssid_2" in params:
        s["sta_ssid_2"] = params["sta_ssid_2"]
    if "sta_password_2" in params and params["sta_password_2"]:
        s["sta_password_2"] = params["sta_password_2"]
    if "sta_ssid_3" in params:
        s["sta_ssid_3"] = params["sta_ssid_3"]
    if "sta_password_3" in params and params["sta_password_3"]:
        s["sta_password_3"] = params["sta_password_3"]

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

    # Cloud settings. The upload destination and device secret are NOT
    # accepted here: they are written only by the activation exchange (or by
    # editing settings.json in maintenance mode for a self-hosted cloud), so
    # a form post can never redirect a device's readings.
    s["cloud_enabled"] = "cloud_enabled" in params
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
