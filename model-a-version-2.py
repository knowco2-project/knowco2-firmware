# knowco2 firmware (AP portal + STA + mDNS)
# Target: Adafruit Feather ESP32-S3 Reverse TFT (CircuitPython 9.2.9)

import time
import board
import displayio
import terminalio
import digitalio
import json
import os
import binascii


import rtc
# ======================================================================
#  STARTUP SPLASH (logo-only, reliable)
#  Put your logo bitmap at: /assets/splash.bmp
#  IMPORTANT: Delete CIRCUITPY/splash.bmp if present (bootloader splash)
# ======================================================================

import gc

SPLASH_BMP = "/assets/splash.bmp"
SPLASH_SECONDS = 4   # change splash duration here (seconds)
SPLASH_BG = 0xFFFFFF   # white background

def _show_logo_splash(display):
    f = None
    try:
        group = displayio.Group()

        bg_bitmap = displayio.Bitmap(1, 1, 1)
        bg_palette = displayio.Palette(1)
        bg_palette[0] = SPLASH_BG
        group.append(displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette))

        f = open(SPLASH_BMP, "rb")
        odb = displayio.OnDiskBitmap(f)
        logo = displayio.TileGrid(odb, pixel_shader=odb.pixel_shader)
        logo.x = (display.width - odb.width) // 2
        logo.y = (display.height - odb.height) // 2
        group.append(logo)

        display.root_group = group
        display.refresh()
        time.sleep(SPLASH_SECONDS)

    except Exception as e:
        print("Splash failed:", e)
    finally:
        try:
            display.root_group = None
        except Exception:
            pass
        if f:
            f.close()
        gc.collect()

# ---- show splash immediately ----
_show_logo_splash(board.DISPLAY)


try:
    import wifi
    import socketpool
except ImportError:
    wifi = None
    socketpool = None

# mDNS is typically built-in on ESP32 CircuitPython (not in the bundle)
try:
    import mdns
except Exception as e:
    mdns = None
    print("mdns IMPORT FAILED:", e)

# Cloud upload deps (HTTPS)
try:
    import ssl
    import adafruit_requests
except Exception as e:
    ssl = None
    adafruit_requests = None
    print("cloud deps IMPORT FAILED:", e)

# HMAC/SHA256 (CircuitPython varies by build)
try:
    import hmac
    import hashlib
    _HAS_HMAC = True
except Exception:
    _HAS_HMAC = False
    try:
        import adafruit_hashlib as hashlib
    except Exception as e:
        hashlib = None
        print("hashlib IMPORT FAILED:", e)

# Battery fuel gauge (I2C)
try:
    import adafruit_max1704x
    print("max1704x lib: OK")
except Exception as e:
    adafruit_max1704x = None
    print("max1704x lib IMPORT FAILED:", e)

try:
    import microcontroller
except Exception:
    microcontroller = None

from adafruit_display_text import label
import adafruit_scd4x

# QR code
try:
    import adafruit_miniqr
except Exception as e:
    adafruit_miniqr = None
    print("miniqr IMPORT FAILED:", e)

LOG_ENABLED = True
_LOG_LAST = {}

FS_READONLY = False
FS_WARNED = False

def log(key, *args, min_interval=5.0):
    if not LOG_ENABLED:
        return
    now = time.monotonic()
    last = _LOG_LAST.get(key, 0.0)
    if (now - last) >= min_interval:
        _LOG_LAST[key] = now
        try:
            print(*args)
        except Exception:
            pass

# ======================================================================
#  CONFIG & CONSTANTS
# ======================================================================

SCD_MEASUREMENT_PERIOD = 5.0
WINDOW_SECONDS = 300.0
WINDOW_SAMPLES = int(WINDOW_SECONDS / SCD_MEASUREMENT_PERIOD) + 1

TREND_DEADBAND = 10.0
TREND_LOOKBACK_SECONDS = 150.0
STATUS_DURATION = 3.0

LOW_THRESHOLD_DEFAULT = 800
MED_THRESHOLD_DEFAULT = 1200
ALERT_THRESHOLD_DEFAULT = 1500

LOW_THRESHOLD = LOW_THRESHOLD_DEFAULT
MED_THRESHOLD = MED_THRESHOLD_DEFAULT
ALERT_THRESHOLD = ALERT_THRESHOLD_DEFAULT

MAX_POINTS_DEFAULT = 1000
MAX_POINTS = MAX_POINTS_DEFAULT

MAX_WEB_POINTS = 2000
SETTINGS_FILE = "settings.json"

SCREEN_MAIN = 0
SCREEN_APINFO = 1
screen = SCREEN_MAIN

# Wi-Fi mode state
WIFI_MODE_AP  = "ap"
WIFI_MODE_STA = "sta"
wifi_mode = WIFI_MODE_AP

# D2 hold threshold for toggling AP/STA
D2_HOLD_SECONDS = 2.0
d2_hold_start = None
d2_hold_fired = False

# ======================================================================
#  BUTTONS / INPUT
# ======================================================================

btn_a = digitalio.DigitalInOut(board.D0)
btn_a.switch_to_input(pull=digitalio.Pull.UP)

btn_b = digitalio.DigitalInOut(board.D1)
btn_b.switch_to_input(pull=digitalio.Pull.DOWN)

btn_c = digitalio.DigitalInOut(board.D2)
btn_c.switch_to_input(pull=digitalio.Pull.DOWN)

def read_a():
    return not btn_a.value

def read_b():
    return btn_b.value

def read_c():
    return btn_c.value

prev_a = False
prev_b = False
prev_c = False

# ======================================================================
#  STATE
# ======================================================================

temp_mode = "F"
display_mode = 0
alerts_enabled = True
graph_scale_mode = "fixed"

last_co2 = None
last_co2_prev = None
last_temp_c = None
last_rh = None

status_timeout = 0.0
co2_history = []

http_server_sock = None
socket_pool = None  # keep SocketPool alive

qr_tilegrid_wifi = None
qr_tilegrid_url = None
qr_caption1 = None
qr_caption2 = None

# QR rebuild cache (prevents flicker)
_last_wifi_payload = None
_last_url_payload = None
_last_qr_target_modules = None
_last_qr_scale = None
_last_qr_right_x = None

ip_str_cached = None      # current local IP (AP IP if AP, STA IP if STA)
mdns_hostname = None
mdns_server = None

hwid_hex = None
board_id_str = None
scd_serial_str = None

fuel_gauge = None
fuel_gauge_kind = None
fuel_bus_name = None

# Cloud globals (applied from settings)
cloud_enabled = False
cloud_api_url = ""
cloud_device_token = ""
cloud_interval_sec = 60

last_cloud_send = 0.0
cloud_failures = 0
CLOUD_MAX_BACKOFF = 10 * 60
# NTP time sync (STA only)
ntp_synced = False
last_ntp_sync = 0.0
NTP_SYNC_INTERVAL = 6 * 60 * 60  # seconds
ntp_sync_pending = True  # try soon after STA connect


# Cloud indicator: show only after a successful post
cloud_last_ok = 0.0
CLOUD_OK_TTL = 300.0  # seconds to keep CLOUD indicator on after success

# Cloud debug
cloud_last_http = None      # last HTTP status code (int) or None
cloud_last_error = ""       # last exception string (short)
cloud_last_attempt_ts = 0   # unix seconds at last attempt


# Pairing (for signup flow)
pair_code = None

# ======================================================================
#  DISPLAY & UI WIDGETS
# ======================================================================

display = board.DISPLAY
display.rotation = 180
main_group = displayio.Group()

bg_bitmap = displayio.Bitmap(display.width, display.height, 1)
bg_palette = displayio.Palette(1)
bg_palette[0] = 0x000000
bg = displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette)
main_group.append(bg)

status_label = label.Label(terminalio.FONT, text="Starting...", color=0xAAAAAA, scale=1)
status_label.anchor_point = (0.5, 0.0)
status_label.anchored_position = (display.width // 2, 0)
main_group.append(status_label)

# Wi-Fi client indicator (shows when connected in STA mode)
wifi_ind_label = label.Label(terminalio.FONT, text="", color=0x00BCD4, scale=1)
wifi_ind_label.anchor_point = (1.0, 0.0)
wifi_ind_label.anchored_position = (display.width - 2, 2)
main_group.append(wifi_ind_label)

# Cloud indicator (shows when device has successfully posted to cloud)
cloud_ind_label = label.Label(terminalio.FONT, text="", color=0x00BCD4, scale=1)
cloud_ind_label.anchor_point = (1.0, 0.0)
cloud_ind_label.anchored_position = (display.width - 2, 14)
main_group.append(cloud_ind_label)


def show_status(msg):
    global status_timeout
    status_label.text = msg
    status_timeout = time.monotonic() + STATUS_DURATION

co2_label = label.Label(terminalio.FONT, text="CO2: ---- ppm", color=0x00FF00, scale=3)
co2_label.anchor_point = (0.5, 0.5)
co2_label.anchored_position = (display.width // 2, display.height // 2 - 20)
main_group.append(co2_label)

th_label = label.Label(terminalio.FONT, text="--.-F  --.-%", color=0xFFFFFF, scale=2)
th_label.anchor_point = (0.5, 0.5)
th_label.anchored_position = (display.width // 2, display.height // 2 + 10)
main_group.append(th_label)

GRAPH_Y = 20
GRAPH_HEIGHT = display.height - GRAPH_Y
GRAPH_WIDTH = display.width

graph_bitmap = displayio.Bitmap(GRAPH_WIDTH, GRAPH_HEIGHT, 6)
graph_palette = displayio.Palette(6)
graph_palette[0] = 0x000000
graph_palette[1] = 0x202020
graph_palette[2] = 0x00FF00
graph_palette[3] = 0xFFFF00
graph_palette[4] = 0xFF0000
graph_palette[5] = 0xFFFFFF

graph = displayio.TileGrid(graph_bitmap, pixel_shader=graph_palette, x=0, y=GRAPH_Y)
main_group.append(graph)

y_min_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
y_min_label.anchor_point = (0.5, 1.0)
y_min_label.anchored_position = (display.width // 2, GRAPH_Y + GRAPH_HEIGHT)
main_group.append(y_min_label)

y_max_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
y_max_label.anchor_point = (0.5, 0.0)
y_max_label.anchored_position = (display.width // 2, GRAPH_Y)
main_group.append(y_max_label)

x_left_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
x_left_label.anchor_point = (0.0, 0.0)
x_left_label.anchored_position = (0, GRAPH_Y + 2)
main_group.append(x_left_label)

x_right_label = label.Label(terminalio.FONT, text="now", color=0x888888, scale=1)
x_right_label.anchor_point = (1.0, 0.0)
x_right_label.anchored_position = (display.width - 1, GRAPH_Y + 2)
main_group.append(x_right_label)

low_label = label.Label(terminalio.FONT, text="LOW", color=0x00FF00, scale=1)
low_label.anchor_point = (0.0, 0.5)
low_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.80))
main_group.append(low_label)

med_label = label.Label(terminalio.FONT, text="MED", color=0xFFFF00, scale=1)
med_label.anchor_point = (0.0, 0.5)
med_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.50))
main_group.append(med_label)

high_label = label.Label(terminalio.FONT, text="HIGH", color=0xFF0000, scale=1)
high_label.anchor_point = (0.0, 0.5)
high_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.20))
main_group.append(high_label)

graph_value_label = label.Label(terminalio.FONT, text="", color=0xFFFFFF, scale=1)
graph_value_label.anchor_point = (0.5, 0.0)
graph_value_label.anchored_position = (display.width // 2, 10)
main_group.append(graph_value_label)

# AP Info screen (also shows STA + mDNS if connected)
ap_ssid_label = label.Label(terminalio.FONT, text="", color=0xFFFFFF, scale=1)
ap_ssid_label.anchor_point = (0.0, 0.0)
ap_ssid_label.anchored_position = (6, 10)
main_group.append(ap_ssid_label)

ap_pass_label = label.Label(terminalio.FONT, text="", color=0x00BCD4, scale=1)
ap_pass_label.anchor_point = (0.0, 0.0)
ap_pass_label.anchored_position = (6, 28)
main_group.append(ap_pass_label)

ap_ip_label = label.Label(terminalio.FONT, text="", color=0xFFFFFF, scale=1)
ap_ip_label.anchor_point = (0.0, 0.0)
ap_ip_label.anchored_position = (6, 70)
main_group.append(ap_ip_label)

ap_batt_label = label.Label(terminalio.FONT, text="", color=0xAAAAAA, scale=1)
ap_batt_label.anchor_point = (0.0, 0.0)
ap_batt_label.anchored_position = (6, 86)
main_group.append(ap_batt_label)

ap_hw_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
ap_hw_label.anchor_point = (0.0, 0.0)
ap_hw_label.anchored_position = (6, 100)
main_group.append(ap_hw_label)

ap_scd_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
ap_scd_label.anchor_point = (0.0, 0.0)
ap_scd_label.anchored_position = (6, 114)
main_group.append(ap_scd_label)

display.root_group = main_group

# ======================================================================
#  SETTINGS & PERSISTENCE
# ======================================================================

settings = {
    "low_threshold": LOW_THRESHOLD_DEFAULT,
    "med_threshold": MED_THRESHOLD_DEFAULT,
    "alert_threshold": ALERT_THRESHOLD_DEFAULT,
    "alerts_enabled": True,
    "graph_scale_mode": "fixed",
    "max_points": MAX_POINTS_DEFAULT,

    "ap_ssid": "",
    "ap_password": "",

    "sta_ssid": "",
    "sta_password": "",

    "device_id": "co2-node-1",
    "temp_mode": "F",
    "display_mode": 0,

    # Cloud telemetry
    "cloud_enabled": False,
    "cloud_api_url": "",
    "cloud_device_token": "",
    "cloud_interval_sec": 60,
}

def apply_settings():
    global LOW_THRESHOLD, MED_THRESHOLD, ALERT_THRESHOLD
    global alerts_enabled, graph_scale_mode, MAX_POINTS
    global cloud_enabled, cloud_api_url, cloud_device_token, cloud_interval_sec

    LOW_THRESHOLD = int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))
    MED_THRESHOLD = int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))
    ALERT_THRESHOLD = int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))

    alerts_enabled = bool(settings.get("alerts_enabled", True))
    graph_scale_mode = settings.get("graph_scale_mode", "fixed")

    try:
        mp = int(settings.get("max_points", MAX_POINTS_DEFAULT))
    except (ValueError, TypeError):
        mp = MAX_POINTS_DEFAULT
    if mp < 100:
        mp = 100
    if mp > 50000:
        mp = 50000
    MAX_POINTS = mp

    cloud_enabled = bool(settings.get("cloud_enabled", False))
    cloud_api_url = (settings.get("cloud_api_url", "") or "").strip()
    cloud_device_token = (settings.get("cloud_device_token", "") or "").strip()
    try:
        cloud_interval_sec = int(settings.get("cloud_interval_sec", 60) or 60)
    except Exception:
        cloud_interval_sec = 60
    if cloud_interval_sec < 15:
        cloud_interval_sec = 15
    if cloud_interval_sec > 3600:
        cloud_interval_sec = 3600

def load_settings():
    global settings, temp_mode, display_mode
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update(data)
    except OSError:
        pass
    except ValueError:
        pass

    apply_settings()

    tm = settings.get("temp_mode", "F")
    if tm in ("F", "C"):
        temp_mode = tm

    dm = settings.get("display_mode", 0)
    try:
        dm_int = int(dm)
    except (ValueError, TypeError):
        dm_int = 0
    if dm_int not in (0, 1, 2):
        dm_int = 0
    display_mode = dm_int

def save_settings():
    global FS_READONLY
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(settings, f)
        try:
            os.replace(tmp, SETTINGS_FILE)
        except AttributeError:
            os.rename(tmp, SETTINGS_FILE)
    except OSError as e:
        try:
            if (e.args and e.args[0] == 30) or getattr(e, "errno", None) == 30:
                FS_READONLY = True
        except Exception:
            pass
        log("save_settings", "Error saving settings.json:", e, min_interval=10.0)
        try:
            os.remove(tmp)
        except Exception:
            pass

def _rand_token(nbytes=4):
    return binascii.hexlify(os.urandom(nbytes)).decode("utf-8").upper()

SAFE32 = "23456789ABCDEFGHJKMNPQRSTUVWXYZU"
def _rand_safe32(n=8):
    b = os.urandom(n)
    return "".join(SAFE32[bb & 31] for bb in b)

def generate_ap_credentials():
    suffix = _rand_token(2)
    ssid = "knowco2-" + suffix
    pw = _rand_safe32(8)
    return ssid, pw

def ensure_ap_credentials():
    ssid = (settings.get("ap_ssid", "") or "").strip()
    pw = (settings.get("ap_password", "") or "").strip()
    if len(ssid) < 1 or len(pw) < 8:
        new_ssid, new_pw = generate_ap_credentials()
        settings["ap_ssid"] = new_ssid
        settings["ap_password"] = new_pw
        save_settings()
        print("Generated AP creds:", new_ssid, new_pw)

def update_settings_from_params(params):
    global temp_mode, display_mode
    ap_changed = False

    old_ap_ssid = settings.get("ap_ssid", "")
    old_ap_pass = settings.get("ap_password", "")

    if "regen_ap" in params:
        new_ssid, new_pw = generate_ap_credentials()
        settings["ap_ssid"] = new_ssid
        settings["ap_password"] = new_pw
        ap_changed = True

    if "low" in params:
        try: settings["low_threshold"] = int(params["low"])
        except ValueError: pass
    if "med" in params:
        try: settings["med_threshold"] = int(params["med"])
        except ValueError: pass
    if "alert" in params:
        try: settings["alert_threshold"] = int(params["alert"])
        except ValueError: pass

    if "max_points" in params:
        try: settings["max_points"] = int(params["max_points"])
        except ValueError: pass

    if "scale" in params and params["scale"] in ("fixed", "wide", "auto"):
        settings["graph_scale_mode"] = params["scale"]

    settings["alerts_enabled"] = "alerts" in params

    if "device_id" in params and params["device_id"]:
        settings["device_id"] = params["device_id"]

    if "ap_ssid" in params and params["ap_ssid"]:
        new_ssid = params["ap_ssid"]
        if new_ssid != old_ap_ssid:
            settings["ap_ssid"] = new_ssid
            ap_changed = True

    if "ap_password" in params and params["ap_password"]:
        new_pass = params["ap_password"]
        if new_pass != old_ap_pass:
            settings["ap_password"] = new_pass
            ap_changed = True

    if "sta_ssid" in params:
        settings["sta_ssid"] = params["sta_ssid"]
    if "sta_password" in params and params["sta_password"]:
        settings["sta_password"] = params["sta_password"]

    if "temp_mode" in params:
        tm = params["temp_mode"]
        if tm in ("F", "C"):
            temp_mode = tm
            settings["temp_mode"] = tm

    if "mode" in params:
        try: dm = int(params["mode"])
        except ValueError: dm = display_mode
        if dm in (0, 1, 2):
            display_mode = dm
            settings["display_mode"] = dm

    # Cloud settings
    settings["cloud_enabled"] = "cloud_enabled" in params
    if "cloud_api_url" in params:
        settings["cloud_api_url"] = params["cloud_api_url"]
    if "cloud_device_token" in params and params["cloud_device_token"]:
        settings["cloud_device_token"] = params["cloud_device_token"]
    elif "cloud_device_secret" in params and params["cloud_device_secret"]:
        # Backward-compatible alias (UI uses "device secret")
        settings["cloud_device_token"] = params["cloud_device_secret"]
    elif "cloud_token" in params and params["cloud_token"]:
        settings["cloud_device_token"] = params["cloud_token"]
    if "cloud_interval_sec" in params:
        try: settings["cloud_interval_sec"] = int(params["cloud_interval_sec"])
        except Exception: pass

    save_settings()
    apply_settings()
    return ap_changed

def mode_name():
    return ["Text Only", "Big CO2", "Graph Only"][display_mode]

# ======================================================================
#  IDs + Pair code + mDNS hostname
# ======================================================================

def init_ids():
    global hwid_hex, board_id_str
    try:
        board_id_str = getattr(board, "board_id", None)
    except Exception:
        board_id_str = None

    try:
        if microcontroller is not None and hasattr(microcontroller, "cpu") and hasattr(microcontroller.cpu, "uid"):
            hwid_hex = binascii.hexlify(microcontroller.cpu.uid).decode("utf-8").upper()
        else:
            hwid_hex = None
    except Exception:
        hwid_hex = None

def init_pair_code():
    global pair_code
    base = (hwid_hex or _rand_token(4))
    tail = base[-6:] if len(base) >= 6 else base
    pair_code = (tail + _rand_safe32(2))[:8]

def init_mdns_hostname():
    global mdns_hostname
    # Keep it DNS-safe: lowercase, hyphenated.
    # Short + readable: knowco2-xxxx (4 chars) to reduce confusion.
    base = (hwid_hex or pair_code or _rand_token(4))
    suffix = (base[-4:] if len(base) >= 4 else base).lower()
    mdns_hostname = ("knowco2-" + suffix).replace("_", "-")

def init_scd_serial(scd_obj):
    global scd_serial_str
    scd_serial_str = None
    try:
        sn = getattr(scd_obj, "serial_number", None)
        if sn is None:
            return
        if callable(sn):
            sn = sn()

        if isinstance(sn, (tuple, list)) and len(sn) > 0 and all(isinstance(x, int) for x in sn):
            if all(0 <= x <= 255 for x in sn):
                scd_serial_str = "".join("%02X" % x for x in sn)
                return
            if all(0 <= x <= 0xFFFF for x in sn):
                scd_serial_str = "-".join("%04X" % (x & 0xFFFF) for x in sn)
                return

        if isinstance(sn, int):
            scd_serial_str = "%08X" % (sn & 0xFFFFFFFF)
        else:
            scd_serial_str = str(sn)

    except Exception as e:
        print("SCD serial read failed:", e)
        scd_serial_str = None


def _friendly_mdns_label(hostname, max_len=64):
    """Return the mDNS URL we want to display on-screen.
    We keep it readable, but we do NOT rename/alias the hostname.
    """
    if not hostname:
        return None
    s = hostname + ".local"
    if max_len and len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


# ======================================================================
#  BATTERY + I2C
# ======================================================================

def scan_i2c(i2c_bus):
    try:
        while not i2c_bus.try_lock():
            pass
        return i2c_bus.scan()
    finally:
        try:
            i2c_bus.unlock()
        except Exception:
            pass

def init_fuel_gauge_on_bus(i2c_bus, bus_name):
    global fuel_gauge, fuel_gauge_kind, fuel_bus_name
    fuel_gauge = None
    fuel_gauge_kind = None
    fuel_bus_name = None

    addrs = scan_i2c(i2c_bus)
    print("I2C scan (%s):" % bus_name, [hex(a) for a in addrs])

    if 0x36 in addrs:
        if adafruit_max1704x is None:
            print("0x36 present but adafruit_max1704x import failed")
        else:
            try:
                fuel_gauge = adafruit_max1704x.MAX17048(i2c_bus)
                try: fuel_gauge.reset()
                except Exception: pass
                try: fuel_gauge.quickstart()
                except Exception: pass
                fuel_gauge_kind = "max17048"
                fuel_bus_name = bus_name
                print("Battery gauge: MAX17048 @0x36 on", bus_name)
                return True
            except Exception as e:
                print("MAX17048 init failed on %s:" % bus_name, e)
    return False

def init_fuel_gauge():
    try:
        if init_fuel_gauge_on_bus(board.I2C(), "board.I2C"):
            return
    except Exception as e:
        print("Battery gauge init on board.I2C failed:", e)

    if hasattr(board, "STEMMA_I2C"):
        try:
            if init_fuel_gauge_on_bus(board.STEMMA_I2C(), "board.STEMMA_I2C"):
                return
        except Exception as e:
            print("Battery gauge init on board.STEMMA_I2C failed:", e)

    print("Battery gauge: not found")

def read_battery():
    if fuel_gauge is None:
        return None, None
    try:
        v = float(fuel_gauge.cell_voltage)
        p = float(fuel_gauge.cell_percent)
        p_i = int(round(p))
        p_i = max(0, min(100, p_i))
        return v, p_i
    except Exception as e:
        print("Battery read error:", e)
        return None, None

# ======================================================================
#  SENSOR
# ======================================================================

i2c = board.I2C()
scd = adafruit_scd4x.SCD4X(i2c)
init_scd_serial(scd)

status_label.text = "Warming up..."
scd.start_periodic_measurement()
time.sleep(5)
status_label.text = "Measuring..."

# ======================================================================
#  UI LOGIC
# ======================================================================

def color_for_co2(co2):
    if co2 < LOW_THRESHOLD:
        return 0x00FF00
    elif co2 < MED_THRESHOLD:
        return 0xFFFF00
    else:
        return 0xFF0000

def graph_color_index_for_co2(val):
    if val < LOW_THRESHOLD:
        return 2
    elif val < MED_THRESHOLD:
        return 3
    else:
        return 4

def apply_alert_colors(co2):
    if not alerts_enabled:
        co2_label.color = 0xFFFFFF
        graph_value_label.color = 0xFFFFFF
        return
    c = color_for_co2(co2)
    co2_label.color = c
    graph_value_label.color = c

def compute_trend_arrow():
    if last_co2 is None:
        return "-"

    try:
        lookback_samples = int(TREND_LOOKBACK_SECONDS / SCD_MEASUREMENT_PERIOD)
    except Exception:
        lookback_samples = 0

    prev = None
    if lookback_samples >= 1 and len(co2_history) > lookback_samples:
        prev = co2_history[-(lookback_samples + 1)]
    elif last_co2_prev is not None:
        prev = last_co2_prev

    if prev is None:
        return "-"

    diff = last_co2 - prev
    if diff > TREND_DEADBAND:
        return "↑"
    elif diff < -TREND_DEADBAND:
        return "↓"
    else:
        return "→"

def refresh_text():
    global last_co2, last_temp_c, last_rh

    if screen != SCREEN_MAIN:
        return

    if display_mode == 2:
        if last_co2 is not None:
            arrow = compute_trend_arrow()
            graph_value_label.text = "%d ppm %s" % (int(last_co2), arrow)
        else:
            graph_value_label.text = "-- ppm"
    else:
        graph_value_label.text = ""

    if last_co2 is None or last_temp_c is None or last_rh is None:
        if display_mode in (0, 1):
            co2_label.text = "CO2: ---- ppm"
            if display_mode == 0:
                th_label.text = "--.-F  --.-%"
        return

    co2 = last_co2
    t_c = last_temp_c
    rh = last_rh
    t_f = t_c * 9 / 5 + 32

    if display_mode == 1:
        co2_label.text = "%d ppm" % int(co2)
    elif display_mode == 0:
        co2_label.text = "CO2: %d ppm" % int(co2)

    if display_mode == 0:
        if temp_mode == "F":
            th_label.text = "%.1fF  %.1f%%" % (t_f, rh)
        else:
            th_label.text = "%.1fC  %.1f%%" % (t_c, rh)

def build_wifi_qr_payload(ssid, pw):
    return "WIFI:T:WPA;S:%s;P:%s;;" % (ssid, pw)

def build_url_qr_payload(ip_str):
    return "http://%s/" % ip_str

def _make_qr_tile(payload, x0, y0, scale=2, target_modules=None):
    if adafruit_miniqr is None:
        return None

    qr = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
    qr.add_data(payload)
    qr.make()

    m = qr.matrix
    modules = m.width

    if target_modules is None:
        target_modules = modules
    if target_modules < modules:
        target_modules = modules

    size = target_modules * scale
    bmp = displayio.Bitmap(size, size, 2)
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = 0xFFFFFF

    off = ((target_modules - modules) // 2) * scale

    for y in range(modules):
        for x in range(modules):
            if m[x, y]:
                ox = off + x * scale
                oy = off + y * scale
                for yy in range(scale):
                    for xx in range(scale):
                        bmp[ox + xx, oy + yy] = 1

    return displayio.TileGrid(bmp, pixel_shader=pal, x=x0, y=y0)

def make_or_update_qrs(ssid, pw, ip_str):
    """Create/refresh the two QR codes on the AP info screen.

    This is relatively expensive (allocations + group edits). To prevent visible
    flicker, we rebuild ONLY when the encoded payload changes.
    """
    global qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2
    global _last_wifi_payload, _last_url_payload, _last_qr_target_modules, _last_qr_scale, _last_qr_right_x

    if adafruit_miniqr is None:
        return

    # Prefer mDNS URL when in STA (friendly + stable), else use IP.
    if wifi_mode == WIFI_MODE_STA and mdns_hostname:
        url_payload = "http://%s.local/" % mdns_hostname
    else:
        url_payload = build_url_qr_payload(ip_str)

    wifi_payload = build_wifi_qr_payload(ssid, pw)

    # If nothing changed, do nothing (prevents flashing).
    if wifi_payload == _last_wifi_payload and url_payload == _last_url_payload:
        return

    _last_wifi_payload = wifi_payload
    _last_url_payload = url_payload

    # Remove old QR objects (if any)
    for obj in (qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2):
        if obj is not None:
            try:
                main_group.remove(obj)
            except Exception:
                pass

    qr_tilegrid_wifi = None
    qr_tilegrid_url = None
    qr_caption1 = None
    qr_caption2 = None

    margin = 2
    shift_left = 60
    caption_h = 8
    cap_pad = 1
    gap = 4

    # Determine module sizes so both QRs use the same target size.
    tmp = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
    tmp.add_data(wifi_payload); tmp.make()
    modules_wifi = tmp.matrix.width

    tmp2 = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
    tmp2.add_data(url_payload); tmp2.make()
    modules_url = tmp2.matrix.width

    target_modules = max(modules_wifi, modules_url)

    avail_h = display.height - (2 * margin)

    def total_height_for(scale_val):
        sz = target_modules * scale_val
        total = (2 * (caption_h + cap_pad + sz)) + gap
        return total, sz

    scale = 4
    total, size = total_height_for(scale)
    if total > avail_h:
        scale = 3
        total, size = total_height_for(scale)
    if total > avail_h:
        scale = 2
        total, size = total_height_for(scale)

    if scale < 3:
        gap = 2
        cap_pad = 0
        caption_h = 8
        scale = 3
        total, size = total_height_for(scale)
        if total > avail_h:
            scale = 2
            total, size = total_height_for(scale)

    right_x = display.width - margin - size - shift_left
    if right_x < 2:
        right_x = 2

    total = (2 * (caption_h + cap_pad + size)) + gap
    top_y = margin + max(0, (avail_h - total) // 2)

    cap1_y = top_y
    qr1_y  = cap1_y + caption_h + cap_pad
    cap2_y = qr1_y + size + gap
    qr2_y  = cap2_y + caption_h + cap_pad

    overflow = (qr2_y + size) - (display.height - margin)
    if overflow > 0:
        cap1_y -= overflow
        qr1_y  -= overflow
        cap2_y -= overflow
        qr2_y  -= overflow

    qr_tilegrid_wifi = _make_qr_tile(wifi_payload, right_x, qr1_y, scale=scale, target_modules=target_modules)
    qr_tilegrid_url  = _make_qr_tile(url_payload,  right_x, qr2_y, scale=scale, target_modules=target_modules)

    if qr_tilegrid_wifi is not None:
        main_group.append(qr_tilegrid_wifi)
    if qr_tilegrid_url is not None:
        main_group.append(qr_tilegrid_url)

    # Captions align to QR start (you asked to bring them over a bit)
    qr_caption1 = label.Label(terminalio.FONT, text="1) Connect", color=0xAAAAAA, scale=1)
    qr_caption1.anchor_point = (0.0, 0.0)
    qr_caption1.anchored_position = (right_x, cap1_y)

    qr_caption2 = label.Label(terminalio.FONT, text="2) Open page", color=0xAAAAAA, scale=1)
    qr_caption2.anchor_point = (0.0, 0.0)
    qr_caption2.anchored_position = (right_x, cap2_y)

    main_group.append(qr_caption1)
    main_group.append(qr_caption2)

    _last_qr_target_modules = target_modules
    _last_qr_scale = scale
    _last_qr_right_x = right_x

def refresh_apinfo_screen():
    ssid = settings.get("ap_ssid", "")
    pw = settings.get("ap_password", "")
    ip = ip_str_cached or "--.--.--.--"

    # Make AP password easier to read *before* the device joins a network.
    # In STA mode we keep the .local line at the normal size.
    try:
        if wifi_mode == WIFI_MODE_AP:
            ap_pass_label.scale = 2
            ap_pass_label.anchored_position = (6, 24)
        else:
            ap_pass_label.scale = 1
            ap_pass_label.anchored_position = (6, 28)
    except Exception:
        pass

    # Show different headline depending on mode
    if wifi_mode == WIFI_MODE_STA:
        ap_ssid_label.text = "STA: " + (settings.get("sta_ssid", "") or "")
        ap_pass_label.text = _friendly_mdns_label(mdns_hostname) or "(mdns off)"
        ap_ip_label.text = "IP:  " + ip
    else:
        ap_ssid_label.text = "SSID: " + ssid
        ap_pass_label.text = pw
        ap_ip_label.text = "IP:   " + ip

    global cached_vbat, cached_pct
    vbat, pct = cached_vbat, cached_pct
    if vbat is None:
        ap_batt_label.text = "Battery: N/A"
    else:
        ap_batt_label.text = "Battery: %.2fV (%d%%)" % (vbat, pct)

    hw = hwid_hex or "N/A"
    hw_short = (hw[:12] + "…") if (hw and len(hw) > 12) else hw

    scd_sn = scd_serial_str or "N/A"
    scd_short = (scd_sn[:12] + "…") if (scd_sn and len(scd_sn) > 12) else scd_sn

    ap_hw_label.text = "HW:  " + (hw_short or "N/A")
    ap_scd_label.text = "SCD: " + (scd_short or "N/A")

    # Keep QR codes in sync with the current mode / address.
    if screen == SCREEN_APINFO and adafruit_miniqr is not None:
        try:
            if wifi_mode == WIFI_MODE_AP:
                make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached or "192.168.4.1")
            else:
                # In STA, URL QR will prefer mDNS automatically inside make_or_update_qrs().
                make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached or "0.0.0.0")
        except Exception as _e:
            pass


def update_wifi_indicator():
    # Shows a small WiFi tag when connected as a client (STA).
    # Shows CLOUD tag only after a successful cloud post (recently).
    try:
        if wifi is not None and wifi_mode == WIFI_MODE_STA and wifi.radio.connected:
            # Prefix with a "T" once NTP has successfully set the device time.
            # Requested display format: "T WIFI".
            try:
                wifi_ind_label.text = "T WIFI" if ntp_synced else "WIFI"
            except Exception:
                wifi_ind_label.text = "WIFI"
        else:
            wifi_ind_label.text = ""
    except Exception:
        wifi_ind_label.text = ""

    # Cloud indicator (only after confirmed post)
    try:
        now = time.monotonic()
        cloud_ok_recent = (cloud_last_ok > 0.0) and ((now - cloud_last_ok) <= CLOUD_OK_TTL)
        if cloud_enabled and wifi_mode == WIFI_MODE_STA and cloud_ok_recent:
            cloud_ind_label.text = "CLOUD"
        else:
            cloud_ind_label.text = ""
    except Exception:
        cloud_ind_label.text = ""
def update_visibility():
    main_visible = (screen == SCREEN_MAIN)
    ap_visible = (screen == SCREEN_APINFO)

    co2_label.hidden = not main_visible
    th_label.hidden = not main_visible

    show_graph = main_visible and (display_mode == 2)
    graph.hidden = not show_graph
    y_min_label.hidden = not show_graph
    y_max_label.hidden = not show_graph
    x_left_label.hidden = not show_graph
    x_right_label.hidden = not show_graph
    low_label.hidden = not show_graph
    med_label.hidden = not show_graph
    high_label.hidden = not show_graph
    graph_value_label.hidden = not show_graph

    if main_visible:
        if display_mode == 0:
            co2_label.scale = 3
            th_label.hidden = False
        elif display_mode == 1:
            co2_label.scale = 4
            th_label.hidden = True

    ap_ssid_label.hidden = not ap_visible
    ap_pass_label.hidden = not ap_visible
    ap_ip_label.hidden = not ap_visible
    ap_batt_label.hidden = not ap_visible
    ap_hw_label.hidden = not ap_visible
    ap_scd_label.hidden = not ap_visible

    for _obj in (qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2):
        if _obj is not None:
            _obj.hidden = not ap_visible

update_visibility()

def update_axis_labels(low, high, span_seconds):
    y_min_label.text = str(int(low))
    y_max_label.text = str(int(high))
    if span_seconds <= 0:
        x_left_label.text = ""
        x_right_label.text = ""
        return
    if span_seconds >= WINDOW_SECONDS:
        x_left_label.text = "t-5.0m"
    else:
        if span_seconds < 90:
            x_left_label.text = "t-%ds" % int(span_seconds)
        else:
            x_left_label.text = "t-%.1fm" % (span_seconds / 60.0)
    x_right_label.text = "now"

def _graph_y_for_value(val, low, high):
    span = max(1, high - low)
    v = max(low, min(val, high))
    frac = (v - low) / span
    h = int(frac * (GRAPH_HEIGHT - 1))
    return GRAPH_HEIGHT - 1 - h

def _set_threshold_label_positions(low, high):
    y_low = _graph_y_for_value(LOW_THRESHOLD, low, high)
    y_med = _graph_y_for_value(MED_THRESHOLD, low, high)
    y_alert = _graph_y_for_value(ALERT_THRESHOLD, low, high)

    low_label.anchored_position = (2, GRAPH_Y + y_low)
    med_label.anchored_position = (2, GRAPH_Y + y_med)
    high_label.anchored_position = (2, GRAPH_Y + y_alert)

    low_label.text = "LOW " + str(int(LOW_THRESHOLD))
    med_label.text = "MED " + str(int(MED_THRESHOLD))
    high_label.text = "HIGH " + str(int(ALERT_THRESHOLD))

def redraw_graph():
    graph_bitmap.fill(0)
    if not co2_history:
        x_left_label.text = ""
        x_right_label.text = ""
        return

    n_total = len(co2_history)
    start_index = max(0, n_total - WINDOW_SAMPLES)
    visible = co2_history[start_index:]
    n = len(visible)

    span_seconds = min(WINDOW_SECONDS, max(0, (n - 1) * SCD_MEASUREMENT_PERIOD))

    if graph_scale_mode == "fixed":
        low, high = 400, 2000
    elif graph_scale_mode == "wide":
        low, high = 400, 3000
    else:
        low = max(400, min(visible))
        high = max(800, max(visible))

    span = max(1, high - low)

    PIXELS_PER_SAMPLE = max(1, GRAPH_WIDTH // WINDOW_SAMPLES)
    used_width = min(GRAPH_WIDTH, n * PIXELS_PER_SAMPLE)
    left_blank = GRAPH_WIDTH - used_width

    for y in [int(GRAPH_HEIGHT * 0.25), int(GRAPH_HEIGHT * 0.5), int(GRAPH_HEIGHT * 0.75)]:
        if 0 <= y < GRAPH_HEIGHT:
            for x in range(GRAPH_WIDTH):
                graph_bitmap[x, y] = 1

    for x in range(left_blank, GRAPH_WIDTH, 20):
        for yy in range(GRAPH_HEIGHT):
            if graph_bitmap[x, yy] == 0:
                graph_bitmap[x, yy] = 1

    latest_x = GRAPH_WIDTH - 1
    latest_y = None

    for k in range(n):
        val = max(low, min(visible[k], high))
        frac = (val - low) / span
        h = int(frac * (GRAPH_HEIGHT - 1))
        color_idx = graph_color_index_for_co2(val)

        x_end = GRAPH_WIDTH - 1 - (n - 1 - k) * PIXELS_PER_SAMPLE
        x_start = x_end - PIXELS_PER_SAMPLE + 1

        if x_end < left_blank:
            continue
        if x_start < left_blank:
            x_start = left_blank

        for x in range(x_start, x_end + 1):
            for yy in range(GRAPH_HEIGHT - 1, GRAPH_HEIGHT - 1 - h, -1):
                graph_bitmap[x, yy] = color_idx

        if k == n - 1:
            latest_y = GRAPH_HEIGHT - 1 - h

    if latest_y is not None:
        for dy in (-1, 0, 1):
            yy = latest_y + dy
            if 0 <= yy < GRAPH_HEIGHT:
                graph_bitmap[latest_x, yy] = 5

    _set_threshold_label_positions(low, high)
    update_axis_labels(low, high, span_seconds)

# ======================================================================
#  NETWORKING & HTTP (raw sockets)
# ======================================================================

def send_all(conn, data, timeout=2.5):
    mv = memoryview(data)
    total = 0
    length = len(mv)
    CHUNK = 512
    start = time.monotonic()

    while total < length:
        if time.monotonic() - start > timeout:
            log("send_to", "send_all timeout at", total, "of", length, "bytes", min_interval=2.0)
            break
        try:
            sent = conn.send(mv[total: total + CHUNK])
        except OSError as e:
            err = e.args[0] if e.args else None
            if err == 11:  # EAGAIN
                time.sleep(0.01)
                continue
            log("send_err", "send_all OSError:", e, min_interval=1.0)
            break
        if sent is None or sent <= 0:
            break
        total += sent

def build_response(status_code, content_type, body_bytes=b""):
    reason = {200:"OK", 204:"No Content", 302:"Found", 404:"Not Found", 405:"Method Not Allowed"}.get(status_code, "OK")
    headers = (
        "HTTP/1.1 %d %s\r\n" % (status_code, reason) +
        "Content-Type: %s\r\n" % content_type +
        "Cache-Control: no-store\r\n" +
        "Pragma: no-cache\r\n" +
        "Connection: close\r\n" +
        "Access-Control-Allow-Origin: *\r\n"
    )
    if status_code != 204:
        headers += "Content-Length: %d\r\n" % len(body_bytes)
    headers += "\r\n"
    return headers.encode("utf-8"), body_bytes

def make_json_response(obj, status=200):
    body = json.dumps(obj).encode("utf-8")
    return build_response(status, "application/json; charset=utf-8", body)

def make_html_response(html_str, status=200):
    body = html_str.encode("utf-8")
    return build_response(status, "text/html; charset=utf-8", body)

def sock_recv(conn, nbytes):
    if hasattr(conn, "recv"):
        return conn.recv(nbytes)
    if hasattr(conn, "recv_into"):
        buf = bytearray(nbytes)
        n = conn.recv_into(buf, nbytes)
        if n is None:
            return b""
        return bytes(buf[:n])
    return b""

def url_decode(s):
    if s is None:
        return ""
    try:
        s = s.replace('+', ' ')
        out = bytearray()
        i = 0
        while i < len(s):
            c = s[i]
            if c == '%' and i + 2 < len(s):
                try:
                    out.append(int(s[i+1:i+3], 16))
                    i += 3
                    continue
                except Exception:
                    pass
            out.extend(c.encode('utf-8'))
            i += 1
        return out.decode('utf-8', 'ignore')
    except Exception:
        return s

def parse_query(path):
    if "?" not in path:
        return path, {}
    route, qs = path.split("?", 1)
    params = {}
    for pair in qs.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        params[url_decode(k)] = url_decode(v)
    return route, params

def _read_request_head(conn, max_bytes=2048, max_wait=0.6):
    data = b""
    start = time.monotonic()
    while (time.monotonic() - start) < max_wait and len(data) < max_bytes:
        try:
            chunk = sock_recv(conn, 512)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data or b"\n\n" in data:
                break
        except OSError:
            time.sleep(0.01)
    return data

_CAPTIVE_PATHS_204 = {
    "/generate_204", "/gen_204", "/ncsi.txt", "/connecttest.txt", "/success.txt", "/hotspot-detect.html",
    "/canonical.html", "/mobile/status.php", "/library/test/success.html", "/fwlink", "/fwlink/", "/redirect",
}

def render_settings_page():
    # keep your existing page structure, but show mdns hint if STA
    data_points = co2_history[-MAX_WEB_POINTS:]
    ints = []
    for v in data_points:
        if v is None:
            continue
        if isinstance(v, (int, float)):
            try:
                ints.append(int(v))
            except (TypeError, ValueError):
                pass
    initial_json = json.dumps(ints)

    checked_alerts = "checked" if settings.get("alerts_enabled", True) else ""
    scale = settings.get("graph_scale_mode", "fixed")
    max_points = int(settings.get("max_points", MAX_POINTS_DEFAULT))
    ap_ssid = settings.get("ap_ssid", "knowco2")
    sta_ssid = settings.get("sta_ssid", "")
    device_id = settings.get("device_id", "co2-node-1")

    cloud_enabled_checked = "checked" if settings.get("cloud_enabled", False) else ""
    cloud_api = settings.get("cloud_api_url", "")

    tm = temp_mode
    dm = display_mode

    def sel_scale(opt):
        return "selected" if scale == opt else ""

    def sel_temp(opt):
        return "selected" if tm == opt else ""

    def sel_mode(opt_int):
        return "selected" if dm == opt_int else ""

    ip_for_hint = ip_str_cached or "192.168.4.1"
    mdns_hint = ""
    if wifi_mode == WIFI_MODE_STA and mdns_hostname:
        mdns_hint = f"<br><small class='muted'>On your home Wi-Fi, you can also use <span class='code'>http://{mdns_hostname}.local/</span>.</small>"

    html = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>CO2 Monitor Settings</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background:#0b0b0b; color:#eee; margin:0; }
    .wrap { max-width:480px; margin:0 auto; padding:16px; }
    h1 { font-size:20px; margin:0 0 10px 0; }
    h2 { font-size:16px; margin:16px 0 8px 0; border-bottom:1px solid #333; padding-bottom:4px; }
    fieldset { border:1px solid #333; border-radius:8px; padding:10px; margin-top:8px; }
    legend { padding:0 4px; font-size:12px; color:#aaa; }
    label { display:block; margin-top:8px; font-size:13px; }
    input, select { width:100%; max-width:260px; padding:4px 6px; border-radius:4px; border:1px solid #444; background:#111; color:#eee; font-size:13px; }
    input[type=checkbox] { width:auto; max-width:none; }
    .row { margin-top:10px; }
    button { padding:6px 12px; border-radius:4px; border:1px solid #00bcd4; background:#00bcd4; color:#000; font-weight:600; cursor:pointer; }
    button:hover { background:#26c6da; border-color:#26c6da; }
    small { color:#aaa; font-size:11px; }
    .muted { color:#aaa; }
    #chart-container { margin-top:12px; border:1px solid #333; border-radius:8px; padding:8px; }
    #chart { width:100%; max-width:420px; height:140px; background:#050505; border-radius:4px; }
    #chart-debug { font-size:10px; color:#888; margin-top:4px; }
    #status-card { border:1px solid #333; border-radius:8px; padding:10px; margin:10px 0; background:#111; }
    #status-main { font-size:18px; margin-bottom:6px; }
    #status-extra { font-size:12px; color:#ccc; }
    .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; border:1px solid #444; margin-right:4px; }
    .badge-low { border-color:#00c853; color:#00e676; }
    .badge-med { border-color:#ffeb3b; color:#fff176; }
    .badge-high { border-color:#ff5252; color:#ff8a80; }
    .code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>CO2 Monitor</h1>
    <div class="muted" style="font-size:12px;margin-bottom:10px;">
      Open <span class="code">http://""" + ip_for_hint + """/</span>.""" + mdns_hint + """
      <br><small class="muted">If your phone says “No Internet”, that’s expected during AP setup.</small>
    </div>

    <div id="status-card">
      <div id="status-main">CO₂: <span id="status-co2">--</span> ppm <span id="status-arrow" title="Trend (↑ rising, ↓ falling, → steady)">-</span></div>
      <div id="status-extra">
        Temp: <span id="status-temp">--.-</span>°
        &nbsp;·&nbsp; RH: <span id="status-rh">--.-</span>%
        <br>
        Air quality: <span id="status-quality" class="badge">unknown</span>
        <br>
        Device ID: <code id="status-device-id">""" + device_id + """</code>
        <div style="margin-top:8px;font-size:12px;color:#ccc;">
          <div>Battery: <span id="status-batt">--</span></div>
          <div>Pair code: <code id="status-pair">--</code></div>
          <div>mDNS: <code id="status-mdns">--</code></div>
        </div>
      </div>
    </div>

    <div id="chart-container">
      <small class="muted">Live CO2 history (last """ + str(MAX_WEB_POINTS) + """ samples max)</small>
      <canvas id="chart" width="420" height="140"></canvas>
      <div id="chart-debug"></div>
    </div>

    <form method="GET" action="/">
      <h2>CO2 &amp; Graph</h2>
      <fieldset>
        <legend>Thresholds</legend>
        <label>Low threshold (ppm)
          <input type="number" name="low" min="400" max="5000"
                 value=\"""" + str(int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))) + """\">
        </label>
        <label>Medium threshold (ppm)
          <input type="number" name="med" min="400" max="5000"
                 value=\"""" + str(int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))) + """\">
        </label>
        <label>Alert threshold (ppm)
          <input type="number" name="alert" min="400" max="5000"
                 value=\"""" + str(int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))) + """\">
        </label>
        <label>Max history points in RAM
          <input type="number" name="max_points" min="100" max="50000"
                 value=\"""" + str(int(max_points)) + """\">
        </label>
        <small>Higher values show longer history but use more memory.</small>
      </fieldset>

      <fieldset class="row">
        <legend>Graph scale</legend>
        <label>Scale mode
          <select name="scale">
            <option value="fixed" """ + sel_scale("fixed") + """>400-2000 ppm (tight)</option>
            <option value="wide" """ + sel_scale("wide") + """>400-3000 ppm (wide)</option>
            <option value="auto" """ + sel_scale("auto") + """>Automatic (based on data)</option>
          </select>
        </label>
      </fieldset>

      <fieldset class="row">
        <legend>Alerts</legend>
        <label>
          <input type="checkbox" name="alerts" value="on" """ + checked_alerts + """>
          Enable color alerts and on-screen alert messages
        </label>
      </fieldset>

      <fieldset class="row">
        <legend>Display &amp; Units</legend>
        <label>Temperature units
          <select name="temp_mode">
            <option value="F" """ + sel_temp("F") + """>Fahrenheit</option>
            <option value="C" """ + sel_temp("C") + """>Celsius</option>
          </select>
        </label>
        <label>Display mode
          <select name="mode">
            <option value="0" """ + sel_mode(0) + """>Text + temp &amp; humidity</option>
            <option value="1" """ + sel_mode(1) + """>Big CO2</option>
            <option value="2" """ + sel_mode(2) + """>Graph-only</option>
          </select>
        </label>
      </fieldset>

      <h2>Wi-Fi Access Point</h2>
      <fieldset>
        <legend>Local AP</legend>
        <label>AP SSID
          <input type="text" name="ap_ssid" maxlength="32"
                 value=\"""" + ap_ssid + """\">
        </label>
        <label>AP password
          <input type="password" name="ap_password" maxlength="63" value="">
        </label>

        <div class="row" style="margin-top:10px;">
          <button type="button" onclick="location.href='/?regen_ap=1'">
            Regenerate AP SSID + Password
          </button>
          <div class="muted" style="margin-top:6px;">
            <small>This restarts AP. View the new password on the device (press D2).</small>
          </div>
        </div>
      </fieldset>

      <h2>Wi-Fi Network (client)</h2>
      <fieldset>
        <legend>For LAN + cloud uploads</legend>
        <label>Network SSID
          <input type="text" name="sta_ssid" maxlength="32"
                 value=\"""" + sta_ssid + """\">
        </label>
        <label>Network password
          <input type="password" name="sta_password" maxlength="63" value="">
        </label>
        <small class="muted">
          Tip: after saving STA credentials, <b>hold D2 for ~2 seconds</b> to switch into STA mode.
        </small>
      </fieldset>

      <h2>Cloud telemetry</h2>
      <fieldset>
        <legend>AWS ingest</legend>
        <label>
          <input type="checkbox" name="cloud_enabled" value="on" """ + cloud_enabled_checked + """>
          Enable cloud uploads (requires STA Wi-Fi + token)
        </label>

        <label>Cloud API URL
          <input type="text" name="cloud_api_url" maxlength="200"
                 placeholder="https://dxxxxxxxxxxxx.cloudfront.net"
                 value=\"""" + cloud_api + """\">
        </label>

        <label>Device token (secret)
          <input type="password" name="cloud_device_token" maxlength="128" value="">
        </label>
        <small>Paste token once. It is stored on device and not shown again.</small>

        <label>Upload interval (seconds)
          <input type="number" name="cloud_interval_sec" min="15" max="3600"
                 value=\"""" + str(int(settings.get("cloud_interval_sec", 60))) + """\">
        </label>
        <small class="muted">
          Pairing: create an account, then enter this device's <b>Pair code</b>.
          The cloud app returns a device token you paste here.
        </small>
      </fieldset>

      <h2>Device identity</h2>
      <fieldset>
        <legend>Telemetry-ready</legend>
        <label>Device ID
          <input type="text" name="device_id" maxlength="40"
                 value=\"""" + device_id + """\">
        </label>
        <small>
          <b>Local endpoints:</b><br>
          • <code>GET /status</code> → live JSON status<br>
          • <code>GET /data</code> → CO₂ history JSON (up to """ + str(MAX_WEB_POINTS) + """ points)
        </small>
      </fieldset>

      <div class="row">
        <button type="submit">Save settings</button>
      </div>
      <div class="row muted">
        <small>Settings are saved to <code>settings.json</code>. If you see “USB mode: settings won't save”, eject CIRCUITPY from your computer.</small>
      </div>
    </form>
  </div>
  <script>
    let lastStatus = null;
    const SAMPLE_PERIOD_SEC = 5;
    const initialPoints = """ + initial_json + """;

    const canvas = document.getElementById('chart');
    const ctx   = canvas.getContext('2d');
    const debugEl = document.getElementById('chart-debug');

    const statusCo2El = document.getElementById('status-co2');
    const statusArrowEl = document.getElementById('status-arrow');
    const statusTempEl = document.getElementById('status-temp');
    const statusRhEl = document.getElementById('status-rh');
    const statusQualityEl = document.getElementById('status-quality');
    const statusDeviceEl = document.getElementById('status-device-id');
    const statusBattEl  = document.getElementById('status-batt');
    const statusPairEl  = document.getElementById('status-pair');
    const statusMdnsEl  = document.getElementById('status-mdns');

    function drawChart(points) {
      const w = canvas.width;
      const h = canvas.height;

      const padL = 34;
      const padR = 6;
      const padT = 10;
      const padB = 18;
      const cw = w - padL - padR;
      const ch = h - padT - padB;

      ctx.fillStyle = '#050505';
      ctx.fillRect(0, 0, w, h);

      if (!points || points.length === 0) {
        ctx.fillStyle = '#ffffff';
        ctx.font = '14px sans-serif';
        ctx.fillText('No data yet', 10, h/2);
        debugEl.textContent = 'Samples: 0';
        return;
      }

      const lowT  = (lastStatus && typeof lastStatus.low_threshold === 'number') ? lastStatus.low_threshold : 800;
      const medT  = (lastStatus && typeof lastStatus.med_threshold === 'number') ? lastStatus.med_threshold : 1200;
      const alertT= (lastStatus && typeof lastStatus.alert_threshold === 'number') ? lastStatus.alert_threshold : 1500;

      let min = Math.min.apply(null, points);
      let max = Math.max.apply(null, points);

      min = Math.min(min, 400, lowT, medT, alertT);
      max = Math.max(max, 800, lowT, medT, alertT);

      if (min === max) { min -= 10; max += 10; }

      function yFor(v) {
        const t = (v - min) / (max - min);
        return padT + (1 - t) * ch;
      }

      function xFor(i) {
        const denom = Math.max(points.length - 1, 1);
        return padL + (i / denom) * cw;
      }

      function segColor(v) {
        if (v < lowT) return '#00e676';
        if (v < medT) return '#fff176';
        if (v < alertT) return '#ff8a80';
        return '#ff5252';
      }

      ctx.globalAlpha = 0.10;
      ctx.fillStyle = '#00e676';
      ctx.fillRect(padL, yFor(lowT), cw, yFor(min) - yFor(lowT));
      ctx.fillStyle = '#fff176';
      ctx.fillRect(padL, yFor(medT), cw, yFor(lowT) - yFor(medT));
      ctx.fillStyle = '#ff5252';
      ctx.fillRect(padL, yFor(max), cw, yFor(medT) - yFor(max));
      ctx.globalAlpha = 1.0;

      const yTicks = [min, lowT, medT, alertT, max];
      const uniq = [];
      for (let i = 0; i < yTicks.length; i++) {
        const v = yTicks[i];
        if (uniq.every(u => Math.abs(u - v) > 1e-6)) uniq.push(v);
      }

      ctx.strokeStyle = '#202020';
      ctx.lineWidth = 1;
      for (let i = 0; i < uniq.length; i++) {
        const yy = yFor(uniq[i]);
        ctx.beginPath();
        ctx.moveTo(padL, yy);
        ctx.lineTo(padL + cw, yy);
        ctx.stroke();
      }

      ctx.strokeStyle = '#444';
      ctx.beginPath();
      ctx.moveTo(padL, padT);
      ctx.lineTo(padL, padT + ch);
      ctx.lineTo(padL + cw, padT + ch);
      ctx.stroke();

      ctx.lineWidth = 2;
      for (let i = 1; i < points.length; i++) {
        const v1 = points[i];
        const x0 = xFor(i-1);
        const x1 = xFor(i);
        const y0 = yFor(points[i-1]);
        const y1 = yFor(v1);
        ctx.strokeStyle = segColor(v1);
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.stroke();
      }

      const spanSec = (points.length - 1) * SAMPLE_PERIOD_SEC;
      debugEl.textContent = 'Samples: ' + points.length + ' · span: ' + (spanSec/60).toFixed(1) + 'm';
    }

    function updateStatusCard(s) {
      lastStatus = s;
      if (!s) return;
      statusCo2El.textContent = (typeof s.co2 === 'number') ? s.co2.toFixed(0) : '--';
      statusArrowEl.textContent = s.trend_arrow || '-';
      statusTempEl.textContent = (typeof s.temp_display === 'number') ? s.temp_display.toFixed(1) : '--.-';
      statusRhEl.textContent = (typeof s.rh === 'number') ? s.rh.toFixed(1) : '--.-';
      statusDeviceEl.textContent = s.device_id || 'co2-node';
      statusPairEl.textContent = s.pair_code || '--';
      statusMdnsEl.textContent = s.mdns || '--';

      let badgeClass = 'badge';
      let label = 'unknown';
      if (typeof s.co2 === 'number') {
        if (s.co2 < s.low_threshold) { badgeClass += ' badge-low'; label = 'Low CO₂'; }
        else if (s.co2 < s.med_threshold) { badgeClass += ' badge-med'; label = 'Medium CO₂'; }
        else { badgeClass += ' badge-high'; label = 'High CO₂'; }
      }
      statusQualityEl.className = badgeClass;
      statusQualityEl.textContent = label;

      if (typeof s.battery_v === 'number' && typeof s.battery_pct === 'number') {
        statusBattEl.textContent = s.battery_v.toFixed(2) + 'V (' + s.battery_pct.toFixed(0) + '%)';
      } else if (typeof s.battery_pct === 'number') {
        statusBattEl.textContent = s.battery_pct.toFixed(0) + '%';
      } else {
        statusBattEl.textContent = '--';
      }
    }

    function refreshChartFromServer() {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/data', true);
      xhr.onreadystatechange = function() {
        if (xhr.readyState === 4) {
          if (xhr.status === 200) {
            try {
              const data = JSON.parse(xhr.responseText);
              const pts = (data && data.co2) ? data.co2 : [];
              drawChart(pts);
            } catch (e) {
              debugEl.textContent = 'Error parsing /data';
            }
          } else {
            debugEl.textContent = 'HTTP ' + xhr.status;
          }
        }
      };
      xhr.send();
    }

    function refreshStatusFromServer() {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/status', true);
      xhr.onreadystatechange = function() {
        if (xhr.readyState === 4 && xhr.status === 200) {
          try { updateStatusCard(JSON.parse(xhr.responseText)); } catch (e) {}
        }
      };
      xhr.send();
    }

    drawChart(initialPoints);
    refreshStatusFromServer();
    setInterval(refreshChartFromServer, 5000);
    setInterval(refreshStatusFromServer, 5000);
  </script>
</body>
</html>"""
    return html

def handle_data_route(conn):
    data_points = co2_history[-MAX_WEB_POINTS:]
    ints = []
    for v in data_points:
        if v is None:
            continue
        if isinstance(v, (int, float)):
            try:
                ints.append(int(v))
            except (TypeError, ValueError):
                pass

    header, body = make_json_response({"co2": ints})
    send_all(conn, header)
    send_all(conn, body)

def handle_status_route(conn):
    if last_temp_c is not None:
        t_c = last_temp_c
        t_f = t_c * 9.0 / 5.0 + 32.0
        temp_display = t_f if temp_mode == "F" else t_c
    else:
        temp_display = None

    arrow = compute_trend_arrow()
    vbat, pct = read_battery()

    payload = {
        "device_id": settings.get("device_id", "co2-node-1"),
        "co2": last_co2,
        "temp_c": last_temp_c,
        "rh": last_rh,
        "temp_mode": temp_mode,
        "temp_display": temp_display,
        "trend_arrow": arrow,

        "display_mode": display_mode,
        "alerts_enabled": alerts_enabled,
        "low_threshold": LOW_THRESHOLD,
        "med_threshold": MED_THRESHOLD,
        "alert_threshold": ALERT_THRESHOLD,
        "history_points": len(co2_history),

        "hwid": hwid_hex,
        "board_id": board_id_str,
        "scd_serial": scd_serial_str,
        "pair_code": pair_code,

        "battery_v": vbat,
        "battery_pct": pct,
        "battery_gauge": fuel_gauge_kind,
        "battery_bus": fuel_bus_name,

        "wifi_mode": wifi_mode,
        "ip": ip_str_cached,
        "mdns": (mdns_hostname + ".local") if (wifi_mode == WIFI_MODE_STA and mdns_hostname) else None,

        "cloud_enabled": cloud_enabled,
        "cloud_interval_sec": cloud_interval_sec,
        "cloud_api_url": cloud_api_url,
        "cloud_configured": bool(cloud_api_url) and bool(cloud_device_token),
        "cloud_last_attempt_ts": cloud_last_attempt_ts,
        "cloud_last_http": cloud_last_http,
        "cloud_last_error": cloud_last_error,
    }

    header, body = make_json_response(payload)
    send_all(conn, header)
    send_all(conn, body)

def handle_root_route(conn, params):
    if params:
        ap_changed = update_settings_from_params(params)
        update_visibility()
        refresh_text()

        # If AP creds changed while in AP mode, restart AP
        if ap_changed and wifi_mode == WIFI_MODE_AP:
            switch_to_ap(force_restart=True)

        if screen == SCREEN_APINFO:
            if wifi_mode == WIFI_MODE_AP:
                make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached or "192.168.4.1")
            refresh_apinfo_screen()

        show_status("AP regenerated" if "regen_ap" in params else "Web settings updated")

    html = render_settings_page()
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)

def start_http_server():
    global http_server_sock
    if wifi is None or socketpool is None:
        return False

    # Recreate server socket
    try:
        if http_server_sock is not None:
            try:
                http_server_sock.close()
            except Exception:
                pass
            http_server_sock = None
    except Exception:
        pass

    global socket_pool
    socket_pool = socketpool.SocketPool(wifi.radio)
    srv = socket_pool.socket(socket_pool.AF_INET, socket_pool.SOCK_STREAM)
    try:
        srv.setsockopt(socket_pool.SOL_SOCKET, socket_pool.SO_REUSEADDR, 1)
    except Exception:
        pass
    srv.bind(("0.0.0.0", 80))
    srv.listen(4)
    try:
        srv.settimeout(0)
    except Exception:
        pass

    http_server_sock = srv
    print("HTTP server listening on 0.0.0.0:80")
    return True

def handle_http_client():
    global http_server_sock
    if http_server_sock is None:
        return

    try:
        conn, addr = http_server_sock.accept()
    except OSError:
        return

    try:
        data = _read_request_head(conn)
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

        if path in _CAPTIVE_PATHS_204:
            header, body = build_response(204, "text/plain; charset=utf-8", b"")
            send_all(conn, header)
            return
        if path in ("/favicon.ico",):
            header, body = build_response(204, "image/x-icon", b"")
            send_all(conn, header)
            return

        if method not in (b"GET", b"HEAD"):
            header, body = build_response(405, "text/plain; charset=utf-8", b"Method Not Allowed")
            send_all(conn, header)
            send_all(conn, body)
            return

        route, params = parse_query(path)

        if route == "/data":
            handle_data_route(conn)
        elif route == "/status":
            handle_status_route(conn)
        else:
            handle_root_route(conn, params)

    except Exception as e:
        log("http_err", "HTTP error:", e, min_interval=1.0)
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ======================================================================
#  Wi-Fi mode switching + mDNS
# ======================================================================

def stop_mdns():
    global mdns_server
    if mdns_server is not None:
        try:
            mdns_server.deinit()
        except Exception:
            pass
        mdns_server = None

def start_mdns_if_possible():
    global mdns_server
    stop_mdns()
    if mdns is None or wifi is None:
        return False
    try:
        # Only makes sense on STA
        if not wifi.radio.connected:
            return False
    except Exception:
        return False
    try:
        mdns_server = mdns.Server(wifi.radio)
        mdns_server.hostname = mdns_hostname or "knowco2"
        mdns_server.advertise_service(service_type="_http", protocol="_tcp", port=80)
        print("mDNS started:", mdns_server.hostname + ".local")
        return True
    except Exception as e:
        print("mDNS start failed:", e)
        mdns_server = None
        return False

def stop_ap():
    if wifi is None:
        return
    try:
        wifi.radio.stop_ap()
    except Exception:
        pass

def disconnect_sta():
    if wifi is None:
        return
    try:
        if wifi.radio.connected:
            wifi.radio.disconnect()
    except Exception:
        pass

def switch_to_ap(force_restart=False):
    global wifi_mode, ip_str_cached

    if wifi is None or socketpool is None:
        show_status("WiFi not available")
        return False

    stop_mdns()
    disconnect_sta()

    try:
        ensure_ap_credentials()
        ssid = settings.get("ap_ssid", "knowco2")
        password = settings.get("ap_password", "")
        if force_restart:
            stop_ap()

        wifi.radio.start_ap(ssid=ssid, password=password)

        ap_ip = None
        for _ in range(50):
            ap_ip = wifi.radio.ipv4_address_ap or wifi.radio.ipv4_address
            if ap_ip is not None:
                break
            time.sleep(0.1)

        if ap_ip is None:
            show_status("WiFi: no AP IP")
            return False

        ip_str_cached = str(ap_ip)
        wifi_mode = WIFI_MODE_AP
        show_status("AP: " + ip_str_cached)
        print("AP started, IP:", ip_str_cached)

        update_wifi_indicator()

        start_http_server()
        if screen == SCREEN_APINFO:
            make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached)
            refresh_apinfo_screen()
        return True

    except Exception as e:
        print("AP start error:", e)
        show_status("AP error")
        return False

def ensure_sta_connected():
    if wifi is None:
        return False
    ssid = (settings.get("sta_ssid") or "").strip()
    pw = (settings.get("sta_password") or "").strip()
    if not ssid or not pw:
        return False

    try:
        if wifi.radio.connected:
            return True
    except Exception:
        pass

    try:
        show_status("WiFi: connecting...")
        wifi.radio.connect(ssid, pw)
        show_status("WiFi: connected")
        return True
    except Exception as e:
        log("sta", "STA connect failed:", e, min_interval=10.0)
        show_status("WiFi: connect fail")
        return False

def switch_to_sta():
    global wifi_mode, ip_str_cached

    if wifi is None or socketpool is None:
        show_status("WiFi not available")
        return False

    stop_ap()
    ok = ensure_sta_connected()
    if not ok:
        return False

    try:
        ip_str_cached = str(wifi.radio.ipv4_address)
    except Exception:
        ip_str_cached = None

    wifi_mode = WIFI_MODE_STA
    show_status("STA: " + (ip_str_cached or "ok"))
    print("STA connected, IP:", ip_str_cached)

    update_wifi_indicator()

    start_http_server()
    start_mdns_if_possible()

    # Kick NTP sync shortly after STA comes up
    global ntp_sync_pending
    ntp_sync_pending = True

    if screen == SCREEN_APINFO:
        refresh_apinfo_screen()
    return True

# ======================================================================
#  CLOUD UPLOAD
# ======================================================================

# FIX 1 + FIX 2:
# - Match backend auth: x-kc2-device-id / x-kc2-ts / x-kc2-sig
# - Signature: base64(HMAC_SHA256(secret_bytes, f"{ts}.{raw_body}"))
# - Reuse SocketPool + requests Session to avoid "Out of sockets"

_cloud_ctx = None
_cloud_session = None

def _b64encode_bytes(raw):
    try:
        import base64
        return base64.b64encode(raw).decode("utf-8")
    except Exception:
        # binascii.b2a_base64 adds a trailing newline; strip it
        return binascii.b2a_base64(raw).decode("utf-8").strip()

def _decode_token_to_bytes(token_str):
    # Token pasted via GET params can turn '+' into ' '.
    if not token_str:
        return None
    t = token_str.strip().replace(" ", "+")
    # allow urlsafe base64 too
    t = t.replace("-", "+").replace("_", "/")
    while len(t) % 4 != 0:
        t += "="
    try:
        return binascii.a2b_base64(t)
    except Exception:
        return None

def _hmac_sha256_digest(key_bytes, msg_bytes):
    if _HAS_HMAC:
        return hmac.new(key_bytes, msg_bytes, hashlib.sha256).digest()

    if hashlib is None:
        return None

    key = key_bytes
    block = 64
    if len(key) > block:
        key = hashlib.sha256(key).digest()
    if len(key) < block:
        key = key + b"\x00" * (block - len(key))

    o_key_pad = bytes((b ^ 0x5C) for b in key)
    i_key_pad = bytes((b ^ 0x36) for b in key)

    inner = hashlib.sha256(i_key_pad + msg_bytes).digest()
    outer = hashlib.sha256(o_key_pad + inner).digest()
    return outer

def _get_cloud_session():
    global _cloud_ctx, _cloud_session, socket_pool
    if wifi is None or socketpool is None or ssl is None or adafruit_requests is None:
        return None

    if socket_pool is None:
        socket_pool = socketpool.SocketPool(wifi.radio)

    if _cloud_ctx is None:
        _cloud_ctx = ssl.create_default_context()

    if _cloud_session is None:
        _cloud_session = adafruit_requests.Session(socket_pool, _cloud_ctx)

    return _cloud_session

def cloud_next_interval():
    base = cloud_interval_sec
    backoff = base * (2 ** min(cloud_failures, 6))
    if backoff > CLOUD_MAX_BACKOFF:
        backoff = CLOUD_MAX_BACKOFF
    if backoff < 15:
        backoff = 15
    return backoff

def cloud_send(payload_dict):
    if not cloud_enabled:
        return False

    if adafruit_requests is None or ssl is None or socketpool is None or wifi is None:
        log("cloud_deps", "Cloud deps missing (ssl/requests/socketpool/wifi)", min_interval=30.0)
        return False

    if not cloud_api_url or not cloud_device_token:
        return False

    # Cloud requires STA
    if wifi_mode != WIFI_MODE_STA:
        return False

    if not ensure_sta_connected():
        return False

    # IMPORTANT: should be the KC2-XXXXXX device id created in the portal
    device_id = (settings.get("device_id") or "").strip()
    if not device_id:
        show_status("Cloud: no device_id")
        return False

    key_bytes = _decode_token_to_bytes(cloud_device_token)
    if not key_bytes:
        show_status("Cloud: bad token")
        return False

    ts = int(time.time())
    global cloud_last_http, cloud_last_error, cloud_last_attempt_ts
    cloud_last_attempt_ts = ts

    # stable JSON so signature matches server exactly
    body = json.dumps(payload_dict, separators=(",", ":"))
    msg = (str(ts) + "." + body).encode("utf-8")

    mac = _hmac_sha256_digest(key_bytes, msg)
    if not mac:
        show_status("Cloud: no crypto")
        return False

    sig_b64 = _b64encode_bytes(mac)

    session = _get_cloud_session()
    if session is None:
        show_status("Cloud: no session")
        return False

    url = cloud_api_url.rstrip("/") + "/v1/ingest"
    headers = {
        "content-type": "application/json",
        "x-kc2-device-id": device_id,
        "x-kc2-ts": str(ts),
        "x-kc2-sig": sig_b64,
    }

    r = None
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
        cloud_last_http = code
        cloud_last_error = ""

        if code == 200:
            return True
        if code in (401, 403):
            show_status("Cloud: auth err")
            return False
        if code == 402:
            show_status("Cloud: inactive")
            return False

        show_status("Cloud HTTP %d" % code)
        return False

    except Exception as e:
        cloud_last_http = None
        cloud_last_error = repr(e)
        log("cloud", "cloud_send error:", e, min_interval=2.0)
        show_status("Cloud: fail")
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

#  NTP TIME SYNC (STA only)
# ======================================================================

_NTP_HOSTS = ("time.cloudflare.com", "time.google.com", "pool.ntp.org")
_NTP_PORT = 123
_NTP_UNIX_DELTA = 2208988800  # seconds between 1900-01-01 and 1970-01-01

def _ntp_query_once(host, timeout=1.5):
    """Return unix epoch seconds (UTC) from an NTP server, or None."""
    if wifi is None or socketpool is None:
        return None
    try:
        global socket_pool
        if socket_pool is None:
            socket_pool = socketpool.SocketPool(wifi.radio)
        pool = socket_pool
        sock = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
        except Exception:
            pass

        # 48-byte NTP request: LI=0, VN=3, Mode=3 -> 0x1B
        req = bytearray(48)
        req[0] = 0x1B

        # Resolve + send
        addr = pool.getaddrinfo(host, _NTP_PORT)[0][-1]
        sock.sendto(req, addr)

        # Receive reply
        resp = bytearray(48)
        n = sock.recv_into(resp, 48)
        if not n or n < 48:
            return None

        # Transmit Timestamp seconds are at bytes 40..43 (big-endian)
        secs = (resp[40] << 24) | (resp[41] << 16) | (resp[42] << 8) | resp[43]
        if secs == 0:
            return None
        unix = int(secs - _NTP_UNIX_DELTA)
        # basic sanity: unix should be after 2020-01-01
        if unix < 1577836800:
            return None
        return unix
    except Exception as e:
        log("ntp_err", "NTP query failed:", host, e, min_interval=10.0)
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass

def ntp_sync(force=False):
    """Best-effort: set RTC from NTP when on STA and connected."""
    global ntp_synced, last_ntp_sync, ntp_sync_pending

    if wifi_mode != WIFI_MODE_STA:
        return False
    if wifi is None:
        return False
    try:
        if not wifi.radio.connected:
            return False
    except Exception:
        return False

    now_mono = time.monotonic()
    if (not force) and ntp_synced and (now_mono - last_ntp_sync) < NTP_SYNC_INTERVAL:
        return True

    for host in _NTP_HOSTS:
        unix = _ntp_query_once(host)
        if unix is None:
            continue
        try:
            rtc.RTC().datetime = time.localtime(unix)
            ntp_synced = True
            last_ntp_sync = now_mono
            ntp_sync_pending = False
            show_status("Time sync: OK")
            return True
        except Exception as e:
            log("ntp_set", "RTC set failed:", e, min_interval=10.0)

    # if we got here, we failed
    last_ntp_sync = now_mono
    if not ntp_synced:
        show_status("Time sync: fail")
    return False

# ======================================================================
#  BOOT
# ======================================================================

init_ids()
init_pair_code()
init_mdns_hostname()

load_settings()
ensure_ap_credentials()
apply_settings()
init_fuel_gauge()

update_visibility()

# Prefer STA if configured, otherwise AP
if (settings.get("sta_ssid") or "").strip() and (settings.get("sta_password") or "").strip():
    if not switch_to_sta():
        switch_to_ap()
else:
    switch_to_ap()

# ======================================================================
#  MAIN LOOP
# ======================================================================

last_sensor = 0.0
last_apinfo_refresh = 0.0
last_batt_refresh = 0.0
last_wifi_ind_refresh = 0.0
cached_vbat = None
cached_pct = None

while True:
    now = time.monotonic()

    if FS_READONLY and not FS_WARNED:
        FS_WARNED = True
        show_status("USB mode: settings won't save")

    if status_timeout > 0 and now > status_timeout:
        status_label.text = ""
        status_timeout = 0.0

    a_now = read_a()
    b_now = read_b()
    c_now = read_c()

    # D0 (A) toggle temp mode on main screen
    if a_now and (not prev_a) and screen == SCREEN_MAIN:
        temp_mode = "C" if temp_mode == "F" else "F"
        settings["temp_mode"] = temp_mode
        save_settings()
        refresh_text()
        show_status("Temp: " + temp_mode)

    # D1 (B) cycle display mode
    if b_now and (not prev_b) and screen == SCREEN_MAIN:
        display_mode = (display_mode + 1) % 3
        settings["display_mode"] = display_mode
        save_settings()
        update_visibility()
        if display_mode == 2:
            try:
                redraw_graph()
            except Exception as e:
                log('graph', 'Graph redraw error:', e, min_interval=2.0)
        refresh_text()
        show_status("Mode: " + mode_name())

    # D2 (C) short press toggles screen, hold toggles Wi-Fi mode
    if c_now and (not prev_c):
        d2_hold_start = now
        d2_hold_fired = False

    if c_now and d2_hold_start is not None and (not d2_hold_fired):
        if (now - d2_hold_start) >= D2_HOLD_SECONDS:
            d2_hold_fired = True
            # Toggle Wi-Fi mode
            if wifi_mode == WIFI_MODE_STA:
                show_status("Switching to AP...")
                switch_to_ap(force_restart=True)
            else:
                show_status("Switching to STA...")
                if not switch_to_sta():
                    show_status("STA failed; AP")
                    switch_to_ap(force_restart=True)

    if (not c_now) and prev_c:
        # released
        if d2_hold_start is not None and (not d2_hold_fired):
            # short press -> toggle screen
            screen = SCREEN_APINFO if screen == SCREEN_MAIN else SCREEN_MAIN
            update_visibility()
            if screen == SCREEN_APINFO:
                if wifi_mode == WIFI_MODE_AP:
                    make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached or "192.168.4.1")
                refresh_apinfo_screen()
            else:
                refresh_text()
                if display_mode == 2:
                    redraw_graph()
        d2_hold_start = None
        d2_hold_fired = False

    prev_a = a_now
    prev_b = b_now
    prev_c = c_now

    if now - last_wifi_ind_refresh > 1.0:
        last_wifi_ind_refresh = now
        update_wifi_indicator()

    if now - last_batt_refresh > 2.0:
        last_batt_refresh = now
        vv, pp = read_battery()
        if vv is not None:
            cached_vbat, cached_pct = vv, pp

    if screen == SCREEN_APINFO and (now - last_apinfo_refresh > 1.0):
        last_apinfo_refresh = now
        refresh_apinfo_screen()

    if now - last_sensor > 0.5:
        last_sensor = now
        if scd.data_ready:
            co2 = scd.CO2
            temp_c = scd.temperature
            rh = scd.relative_humidity

            last_co2_prev = last_co2
            last_co2 = co2
            last_temp_c = temp_c
            last_rh = rh

            if screen == SCREEN_MAIN:
                refresh_text()
                if last_co2 is not None:
                    apply_alert_colors(last_co2)
                    if alerts_enabled and last_co2 >= ALERT_THRESHOLD:
                        show_status("ALERT: %d ppm" % int(last_co2))

            co2_history.append(last_co2)
            if len(co2_history) > MAX_POINTS:
                co2_history[:] = co2_history[-MAX_POINTS:]

            if screen == SCREEN_MAIN and display_mode == 2:
                redraw_graph()
    # NTP sync (STA only) - keep timestamps valid for cloud auth
    if wifi_mode == WIFI_MODE_STA:
        try:
            due = (not ntp_synced) or ntp_sync_pending or ((now - last_ntp_sync) > NTP_SYNC_INTERVAL)
        except Exception:
            due = True
        if due:
            # Quick attempt; uses short socket timeouts
            ntp_sync(force=False)

        # Cloud upload (periodic) - STA only
        if cloud_enabled and wifi_mode == WIFI_MODE_STA:
            interval = cloud_next_interval()
            if now - last_cloud_send > interval:
                last_cloud_send = now
                payload = {
                    "device_id": settings.get("device_id", "co2-node-1"),
                    "ts": int(time.time()),
                    "co2": last_co2,
                    "temp_c": last_temp_c,
                    "rh": last_rh,
                    "battery_pct": cached_pct,
                    "battery_v": cached_vbat,
                    "hwid": hwid_hex,
                    "scd_serial": scd_serial_str,
                    "board_id": board_id_str,
                }
                ok = cloud_send(payload)
                if ok:
                    cloud_failures = 0
                    cloud_last_ok = time.monotonic()
                    show_status("Cloud: OK")
                else:
                    cloud_failures += 1

    handle_http_client()
    time.sleep(0.02)

