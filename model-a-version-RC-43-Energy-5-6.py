# knowco2 firmware (AP portal + STA + mDNS)
# Target: Adafruit Feather ESP32-S3 Reverse TFT (CircuitPython 10.x)
# Version: RC-43-Energy
# ----------------------------------------------------------------------
# FEATURE SUMMARY
# - Splash screen with centered logo bitmap and automatic cleanup.
# - CO₂/temperature/humidity sensing with SCD4x (periodic measurement).
# - Three display modes: text summary, big CO₂ number, and live graph.
# - Graph history with fixed/wide/auto scale, thresholds, and trend arrow.
# - Button controls: A toggles °C/°F, B cycles display mode, C toggles
#   main/AP info screens (long-press switches Wi-Fi mode).
# - Battery fuel-gauge monitoring and percent/voltage display.
# - Alert thresholds with color-coded UI and on-screen alert banner.
# - STA/AP Wi-Fi modes with QR codes and mDNS hostname (knowco2-xxxx.local).
# - HTTP configuration portal for Wi-Fi, alerts, calibration, and device ID.
# - Settings persist in settings.json with automatic backup/restore across OTA.
# - NTP time sync (STA mode) and optional HTTPS cloud upload with HMAC auth.
# - MQTT publishing to any broker (Home Assistant, Mosquitto, etc.).
# - Adafruit IO publishing; MQTT and cloud can run simultaneously.
# - CSV export at /export.csv; calibration at /calibration.
# - OTA firmware update at /update: accepts a .py file (firmware only) or a
#   .zip package (firmware + libs + boot.py + assets).  ZIP format supports
#   both STORED and DEFLATE compression.
# - Watchdog is extended to 90 s and fed per-chunk during OTA writes so a
#   large upload can never cause a mid-write reset or filesystem wipe.
# - Web UI fully internationalised: 9 languages (en, es, fr, de, pt, it,
#   ja, zh, ko).  Language preference stored in localStorage.
# - Full accessibility: ARIA roles, aria-describedby, skip-to-content link,
#   explicit label/input associations, 44 px tap targets, 3 px focus rings,
#   prefers-reduced-motion support.
# - Security headers on all responses: X-Content-Type-Options,
#   X-Frame-Options, Referrer-Policy.
# - Admin password protection for the settings page (POST-based auth).
# - Admin password protection for /calibration write operations.
# - CircuitPython runtime version detected at boot and shown on info screen
#   and included in /status JSON alongside firmware version.
# - /status JSON omits cloud_api_url (internal endpoint) for network privacy.
# ----------------------------------------------------------------------

FIRMWARE_VERSION = "RC-43-Energy-v5"

import time
import board
import displayio
import terminalio
import digitalio
import json
import os
import binascii
import sys as _sys

# Detect CircuitPython runtime version (e.g. "10.0.3") for display and diagnostics.
try:
    _impl = getattr(_sys, "implementation", None)
    if _impl is not None and hasattr(_impl, "version"):
        _v = _impl.version
        if isinstance(_v, tuple) and len(_v) >= 3:
            cp_version_str = "%d.%d.%d" % (_v[0], _v[1], _v[2])
        else:
            cp_version_str = str(_v)
    else:
        _sv = getattr(_sys, "version", "") or ""
        _idx = _sv.find("CircuitPython ")
        if _idx >= 0:
            _tail = _sv[_idx + len("CircuitPython "):]
            cp_version_str = _tail.split()[0].rstrip(";")
        else:
            cp_version_str = _sv.split(";")[0].strip() or "unknown"
except Exception:
    cp_version_str = "unknown"

# MQTT (optional — install adafruit_minimqtt on CIRCUITPY/lib/)
try:
    import adafruit_minimqtt.adafruit_minimqtt as MQTT
    _HAS_MQTT = True
except Exception:
    MQTT = None
    _HAS_MQTT = False
    print("adafruit_minimqtt not available; MQTT/AIO disabled")

BOOT_TIME_MONO = time.monotonic()

# Make sure no socket operation can block forever (helps prevent internal watchdog expiry).
try:
    import socket as _socket
    _socket.setdefaulttimeout(5)  # seconds
except Exception:
    pass


import storage
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

# --- Memory monitor (debug) ---
MEM_MONITOR_INTERVAL_S = 20
mem_free_min = 1_000_000_000
mem_free_max = 0
mem_free_ema = 0.0
mem_samples = 0
last_gc_ts = 0.0

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
            # Setting root_group to None can dereference a null pointer inside
            # CircuitPython's C-level display driver, causing a hard fault that
            # bypasses Python exception handling.  Use an empty Group instead.
            display.root_group = displayio.Group()
        except Exception:
            pass
        if f:
            f.close()
        gc.collect()

# ---- show splash immediately ----
# Rotate the display before showing the splash so the logo appears upright on the Reverse TFT.
# The UI code later sets display.rotation = 180 for the main interface; this ensures the splash matches.
try:
    board.DISPLAY.rotation = 180
except Exception:
    pass
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

# Sensor drivers — both are optional; whichever is present wins at runtime.
try:
    import adafruit_scd4x
except Exception:
    adafruit_scd4x = None

try:
    import adafruit_scd30
except Exception:
    adafruit_scd30 = None

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


def _ensure_fs_writable():
    """Try to make CIRCUITPY writable (works when USB mass storage is not mounted by a host)."""
    global FS_READONLY
    try:
        storage.remount("/", readonly=False)
    except Exception:
        pass
    try:
        FS_READONLY = bool(storage.getmount("/").readonly)
    except Exception:
        pass

# Try once at boot
_ensure_fs_writable()

def log(key, *args, min_interval=5.0):
    if not LOG_ENABLED:
        return
    now = time.monotonic()
    last = _LOG_LAST.get(key, 0)
    if (now - last) < min_interval:
        return
    _LOG_LAST[key] = now
    print(key + ":", *args)


def _as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_int(value, min_val, max_val, default):
    iv = _as_int(value, default)
    if iv is None:
        return default
    if iv < min_val:
        return min_val
    if iv > max_val:
        return max_val
    return iv


def _safe_setattr(obj, name, value):
    try:
        setattr(obj, name, value)
        return True
    except Exception:
        return False


def _safe_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return None

def _scd_set_ambient_pressure(sensor, hpa):
    """Set ambient pressure on any supported sensor family."""
    try:
        if hasattr(sensor, "set_ambient_pressure"):
            sensor.set_ambient_pressure(int(hpa))    # SCD-4x
        else:
            sensor.ambient_pressure = int(hpa)       # SCD-30
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

# Sensor freeze detection: show "SENSOR ERR" banner after SENSOR_FROZEN_WARN_SEC with no data;
# force an MCU reset (last resort) after SENSOR_HARD_RESET_SEC.
SENSOR_FROZEN_WARN_SEC = 30.0
SENSOR_HARD_RESET_SEC = 90.0

# Minimum seconds between STA reconnect attempts — wifi.radio.connect() can block
# 10-30 s, so without this cooldown it would starve sensor polling and NTP.
STA_RECONNECT_COOLDOWN_S = 60.0

# Minimum seconds between NTP attempts — without this a failed sync would retry on
# every main-loop iteration, blocking up to 4.5 s each time.
NTP_MIN_RETRY_S = 60.0

LOW_THRESHOLD_DEFAULT = 800
MED_THRESHOLD_DEFAULT = 1200
ALERT_THRESHOLD_DEFAULT = 1500

# ── Color schemes ──────────────────────────────────────────────────────────
# NORMAL: standard red/yellow/green traffic-light scheme.
# CB (colorblind-safe): Wong palette — distinguishable for deuteranopia,
# protanopia, and tritanopia (sky-blue / amber / vermillion).
_SCHEME_NORMAL = {"low": 0x00FF00, "med": 0xFFFF00, "alert": 0xFF0000}
_SCHEME_CB     = {"low": 0x56B4E9, "med": 0xE69F00, "alert": 0xD55E00}
_active_scheme = _SCHEME_NORMAL  # updated by apply_color_scheme()

# ── Low Power / Energy Saver mode ─────────────────────────────────────────
# Activated by holding button A (D0) for LP_A_HOLD_SECONDS (2 s).
# Short-press still toggles °C/°F.  Hold again to exit LP mode.
# All normal features stay active; measurement rate, upload frequency,
# and display brightness are reduced to extend battery life.
LP_A_HOLD_SECONDS     = 2.0     # seconds to hold A to toggle LP mode
ENERGY_LP_BRIGHTNESS  = 0.20    # display brightness in LP mode (20 %)
ENERGY_LP_SLEEP_S     = 0.05    # main-loop sleep in LP mode (vs 0.01 s normal)
ENERGY_LP_CLOUD_MULT  = 5       # cloud upload interval multiplier in LP mode
ENERGY_LP_MQTT_MULT   = 5       # MQTT interval multiplier in LP mode
ENERGY_LP_AIO_MULT    = 5       # Adafruit IO interval multiplier in LP mode
# Battery health thresholds
BATT_WARN_PCT         = 15      # show LOW-BATT banner below this percent
BATT_CRIT_PCT         = 5       # critical — uploads reduced a further 2x
BATT_BOOT_WARN_V      = 3.20    # show charging screen at boot below this voltage

LOW_THRESHOLD = LOW_THRESHOLD_DEFAULT
MED_THRESHOLD = MED_THRESHOLD_DEFAULT
ALERT_THRESHOLD = ALERT_THRESHOLD_DEFAULT

MAX_POINTS_DEFAULT = 1000
MAX_POINTS = MAX_POINTS_DEFAULT

MAX_WEB_POINTS = 2000
SETTINGS_FILE = "settings.json"

SCREEN_MAIN = 0
SCREEN_APINFO = 1
SCREEN_REGULATORY = 2   # FCC / regulatory e-label (hold B on info screen)
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
qr_page_indicator = None  # small "1/2" label showing current page

# 0 = WiFi/join QR  1 = URL/open QR  (cycles on short-press when on SCREEN_APINFO + AP mode)
_qr_page = 0

# QR rebuild cache (prevents flicker)
_last_wifi_payload = None
_last_url_payload = None
_last_qr_page = None  # track page changes so a page flip forces a rebuild
_last_qr_target_modules = None
_last_qr_scale = None
_last_qr_right_x = None

ip_str_cached = None      # current local IP (AP IP if AP, STA IP if STA)
mdns_hostname = None
mdns_server = None
_wd = None                # hardware watchdog; initialized near the main loop

hwid_hex = None
board_id_str = None
scd_serial_str = None
sensor_model_str = "SCD"  # updated during init to "SCD41", "SCD40", "SCD30", etc.

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

# Monotonic timestamp of the last STA reconnect attempt (for cooldown).
last_sta_reconnect_attempt = 0.0

# Auto-reconnect state: if startup STA fails, the main loop retries periodically.
_sta_fallback = False        # True when startup STA failed; cleared on success or user AP choice
_sta_auto_retry_count = 0   # number of background attempts since last boot
last_sta_auto_retry = 0.0   # monotonic time of last auto-retry
_STA_AUTO_RETRY_INTERVAL = 90.0  # seconds between background STA reconnect tries
_STA_AUTO_RETRY_MAX = 10         # stop after this many tries (~15 min total)

# Monotonic timestamp of the last NTP attempt (success or failure) for rate-limiting.
last_ntp_attempt = 0.0

# True when the sensor has been frozen long enough to show the error banner.
sensor_frozen_shown = False

# Track whether a CO₂ alert has already been shown. This prevents repeatedly
# re-triggering the alert status message on every sample when the CO₂
# concentration stays above the threshold. It is reset when the value
# falls below the threshold.
alert_triggered = False

# ── Energy (Low Power) mode state ──────────────────────────────────────────
energy_mode = False            # True while LP mode is active
_scd_period_effective = SCD_MEASUREMENT_PERIOD  # 5.0 normal, 30.0 in LP mode
_btn_a_hold_start = None      # monotonic time when A went down (for long-press)
_btn_a_hold_fired = False     # True once the long-press LP toggle has fired
_btn_b_hold_start = None      # monotonic time when B went down (for regulatory screen)
_btn_b_hold_fired = False     # True once the B hold has fired
B_HOLD_SECONDS = 2.0          # seconds to hold B on info screen to open regulatory

# Current rate of change of CO₂ (ppm per second).  This is computed whenever
# a new sensor sample is read and used to display the rate of change next
# to the trend arrow on the graph screen.  If no previous sample exists,
# this is set to None.
rate_of_change = None

# Indicates whether a graph redraw is currently in progress.  When True,
# additional calls to redraw_graph() will be skipped to avoid overloading
# the display and to improve responsiveness when the user quickly cycles
# display modes.  It is reset to False once the redraw completes.
graph_drawing = False

# Pending button-B press: set True when a rising edge is captured inside a
# blocking operation (graph redraw, MQTT send, HTTP response) so the press
# is not silently dropped before the main loop runs again.
_btn_b_pending = False

# When True, a graph redraw has been requested.  This flag is set when
# the user switches into graph display mode or when new CO₂ data arrives
# while the graph is visible.  The redraw itself will occur later in
# the main loop when the system is idle, ensuring responsive button
# handling.
graph_refresh_needed = False

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

status_label = label.Label(terminalio.FONT, text="", color=0xAAAAAA, scale=1)
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

# ----------------------------------------------------------------------
# Replace text-based WiFi and cloud indicators with custom low-height bitmaps.
#
# To reduce the vertical footprint of the WiFi and cloud status indicators
# without abbreviating their text, we define a tiny 4x5 pixel font and
# compose bitmap-based text for "WIFI", "TWIFI", and "CLOUD".  These
# bitmaps are about five pixels tall rather than the eight pixels of the
# built-in terminal font.  We then create TileGrids for each and
# position them at the top right of the screen.  Their visibility is
# controlled in update_wifi_indicator().

# Define 4x5 pixel patterns for each character required.
_SMALL_FONT_4x5 = {
    "W": [
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 1, 1),
        (1, 1, 0, 1),
        (1, 0, 0, 1),
    ],
    "I": [
        (0, 1, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 1, 1, 0),
    ],
    "F": [
        (1, 1, 1, 1),
        (1, 0, 0, 0),
        (1, 1, 1, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
    ],
    "T": [
        (1, 1, 1, 1),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
    ],
    "C": [
        (0, 1, 1, 1),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (0, 1, 1, 1),
    ],
    "L": [
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 1, 1, 1),
    ],
    "O": [
        (0, 1, 1, 0),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (0, 1, 1, 0),
    ],
    "U": [
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (0, 1, 1, 0),
    ],
    "D": [
        (1, 1, 1, 0),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 1, 1, 0),
    ],
}

def _make_small_text_bitmap(text):
    """Create a small bitmap and palette for the given text using the
    4x5 font defined above. Returns (bitmap, palette, width, height).

    Each character is drawn in a 4x5 grid with a one-pixel spacer
    column between characters.  Palette index 0 is transparent and
    index 1 is the cyan color used by WiFi/cloud indicators."""
    char_width = 4
    char_height = 5
    spacing = 1
    num_chars = len(text)
    width = num_chars * char_width + (num_chars - 1) * spacing
    height = char_height
    bmp = displayio.Bitmap(width, height, 2)
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = 0x00BCD4
    x_off = 0
    for idx, ch in enumerate(text):
        pattern = _SMALL_FONT_4x5.get(ch.upper(), None)
        if pattern:
            for y in range(char_height):
                row = pattern[y]
                for x in range(char_width):
                    if row[x]:
                        bmp[x_off + x, y] = 1
        x_off += char_width
        if idx < num_chars - 1:
            x_off += spacing
    return bmp, pal, width, height

# Build the small-text bitmaps for WiFi and cloud indicators.  We
# construct two WiFi variants: one for plain "WIFI" and another for
# "TWIFI" (prefix T indicates NTP time sync).  We also build a
# cloud text for "CLOUD".  The widths of the bitmaps are used to
# position them flush against the right edge of the display.
# ── TWC status indicator ───────────────────────────────────────────────────
# Three independently-colored single-character labels in the top-right corner.
# T = NTP-synced STA mode  |  W = WiFi connected  |  C = Cloud recently uploaded
# Active color: teal 0x00BCD4.  Inactive: dim 0x333333.
# Laid out right-to-left so they read "T W C" from left to right.
_TWC_ACTIVE = 0x00BCD4
_TWC_DIM    = 0x2A2A2A
_TWC_GAP    = 6   # px per character at scale=1 (terminalio.FONT glyph width)

twc_c_label = label.Label(terminalio.FONT, text="C", color=_TWC_DIM, scale=1)
twc_c_label.anchor_point = (1.0, 0.0)
twc_c_label.anchored_position = (display.width - 2, 2)
main_group.append(twc_c_label)

twc_w_label = label.Label(terminalio.FONT, text="W", color=_TWC_DIM, scale=1)
twc_w_label.anchor_point = (1.0, 0.0)
twc_w_label.anchored_position = (display.width - 2 - _TWC_GAP, 2)
main_group.append(twc_w_label)

twc_t_label = label.Label(terminalio.FONT, text="T", color=_TWC_DIM, scale=1)
twc_t_label.anchor_point = (1.0, 0.0)
twc_t_label.anchored_position = (display.width - 2 - _TWC_GAP * 2, 2)
main_group.append(twc_t_label)


def show_status(msg):
    global status_timeout
    status_label.text = msg
    status_timeout = time.monotonic() + STATUS_DURATION

# Persistent "SENSOR ERR" banner — unlike show_status() it stays visible until
# the sensor recovers, ensuring the user always knows when readings have stopped.
sensor_frozen_label = label.Label(terminalio.FONT, text="!! SENSOR ERR", color=0xFF4400, scale=1)
sensor_frozen_label.anchor_point = (0.5, 1.0)
sensor_frozen_label.anchored_position = (display.width // 2, display.height - 2)
sensor_frozen_label.hidden = True
main_group.append(sensor_frozen_label)

# Low-power mode badge — sits immediately left of the TWC indicator cluster.
# TWC uses 3 × 6px chars + 2px right margin = 20px from right edge.
# Add 4px gap → LP right edge at display.width - 24.
lp_badge_label = label.Label(terminalio.FONT, text="LP", color=0x00FF88, scale=1)
lp_badge_label.anchor_point = (1.0, 0.0)
lp_badge_label.anchored_position = (display.width - 2 - _TWC_GAP * 3 - 4, 2)
lp_badge_label.hidden = True
main_group.append(lp_badge_label)

# Low battery warning banner — appears at the bottom of the main screen.
batt_warn_label = label.Label(terminalio.FONT, text="!! LOW BATT", color=0xFF8800, scale=1)
batt_warn_label.anchor_point = (0.5, 1.0)
batt_warn_label.anchored_position = (display.width // 2, display.height - 12)
batt_warn_label.hidden = True
main_group.append(batt_warn_label)

co2_label = label.Label(terminalio.FONT, text="CO2: ---- ppm", color=0x00FF00, scale=3)
co2_label.anchor_point = (0.5, 0.5)
co2_label.anchored_position = (display.width // 2, display.height // 2 - 20)
main_group.append(co2_label)

# Separate "ppm" label used only in Big CO2 mode so the number can be
# scaled as large as possible without the "ppm" suffix eating screen space.
ppm_label = label.Label(terminalio.FONT, text="ppm", color=0x00FF00, scale=3)
ppm_label.anchor_point = (0.5, 0.0)
ppm_label.anchored_position = (display.width // 2, display.height - 28)
ppm_label.hidden = True
main_group.append(ppm_label)

th_label = label.Label(terminalio.FONT, text="--.-F  --.-%", color=0xFFFFFF, scale=2)
th_label.anchor_point = (0.5, 0.5)
th_label.anchored_position = (display.width // 2, display.height // 2 + 22)
main_group.append(th_label)

GRAPH_Y = 20
GRAPH_HEIGHT = display.height - GRAPH_Y
GRAPH_MARGIN = 30                           # px reserved on the left for Y-axis labels
GRAPH_WIDTH = display.width - GRAPH_MARGIN  # graph bitmap width (210 px)

graph_bitmap = displayio.Bitmap(GRAPH_WIDTH, GRAPH_HEIGHT, 7)
graph_palette = displayio.Palette(7)
graph_palette[0] = 0x000000
graph_palette[1] = 0x202020
graph_palette[2] = _SCHEME_NORMAL["low"]
graph_palette[3] = _SCHEME_NORMAL["med"]
graph_palette[4] = _SCHEME_NORMAL["alert"]
graph_palette[5] = 0xFFFFFF  # latest-point dot
graph_palette[6] = 0x666666  # Y-axis and X-axis border lines

graph = displayio.TileGrid(graph_bitmap, pixel_shader=graph_palette,
                            x=GRAPH_MARGIN, y=GRAPH_Y)
main_group.append(graph)

y_min_label = label.Label(terminalio.FONT, text="t-5.0m", color=0x00B4D8, scale=1)
y_min_label.anchor_point = (0.0, 1.0)
y_min_label.anchored_position = (2, GRAPH_Y + GRAPH_HEIGHT - 1)
main_group.append(y_min_label)

y_max_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
y_max_label.anchor_point = (0.0, 0.0)
y_max_label.anchored_position = (2, GRAPH_Y)
main_group.append(y_max_label)

# Static Y-axis label at top-left — replaces the old "t-5.0m" span label.
# Tells the user that the left axis is CO₂ in ppm.
x_left_label = label.Label(terminalio.FONT, text="CO2 ppm", color=0x888888, scale=1)
x_left_label.anchor_point = (0.0, 0.0)
x_left_label.anchored_position = (2, 2)
main_group.append(x_left_label)

x_right_label = label.Label(terminalio.FONT, text="now", color=0x00B4D8, scale=1)
x_right_label.anchor_point = (1.0, 1.0)
x_right_label.anchored_position = (display.width - 1, GRAPH_Y + GRAPH_HEIGHT - 1)
main_group.append(x_right_label)

# Midpoint time label — static "-2.5m" since the window is always 5 minutes.
x_mid_label = label.Label(terminalio.FONT, text="-2.5m", color=0x00B4D8, scale=1)
x_mid_label.anchor_point = (0.5, 1.0)
x_mid_label.anchored_position = (GRAPH_MARGIN + GRAPH_WIDTH // 2, GRAPH_Y + GRAPH_HEIGHT - 1)
x_mid_label.hidden = True
main_group.append(x_mid_label)

low_label = label.Label(terminalio.FONT, text="LOW", color=_SCHEME_NORMAL["low"], scale=1)
low_label.anchor_point = (0.0, 0.5)
low_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.80))
main_group.append(low_label)

med_label = label.Label(terminalio.FONT, text="MED", color=_SCHEME_NORMAL["med"], scale=1)
med_label.anchor_point = (0.0, 0.5)
med_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.50))
main_group.append(med_label)

high_label = label.Label(terminalio.FONT, text="HIGH", color=_SCHEME_NORMAL["alert"], scale=1)
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
ap_ip_label.anchored_position = (6, 58)
main_group.append(ap_ip_label)

ap_batt_label = label.Label(terminalio.FONT, text="", color=0xAAAAAA, scale=1)
ap_batt_label.anchor_point = (0.0, 0.0)
ap_batt_label.anchored_position = (6, 74)
main_group.append(ap_batt_label)

ap_hw_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
ap_hw_label.anchor_point = (0.0, 0.0)
ap_hw_label.anchored_position = (6, 88)
main_group.append(ap_hw_label)

ap_scd_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
ap_scd_label.anchor_point = (0.0, 0.0)
ap_scd_label.anchored_position = (6, 102)
main_group.append(ap_scd_label)

ap_fw_label = label.Label(terminalio.FONT, text="", color=0x666666, scale=1)
ap_fw_label.anchor_point = (0.0, 0.0)
ap_fw_label.anchored_position = (6, 116)
main_group.append(ap_fw_label)

# --- Regulatory / FCC e-label screen (SCREEN_REGULATORY) ---
# Shown when user holds B for 2s while on the info screen (SCREEN_APINFO).
# Satisfies FCC 47 CFR §2.935 electronic labeling requirement.
# Access path: Info screen → hold B 2s  (≤2 steps from settings, within the 3-step rule)
reg_title_label = label.Label(terminalio.FONT, text="-- REGULATORY INFO --",
                               color=0x666666, scale=1)
reg_title_label.anchor_point = (0.0, 0.0)
reg_title_label.anchored_position = (6, 6)
reg_title_label.hidden = True
main_group.append(reg_title_label)

reg_fcc_label = label.Label(terminalio.FONT, text="FCC ID: 2AC7Z-ESPS3MINI1",
                             color=0xFFFFFF, scale=1)
reg_fcc_label.anchor_point = (0.0, 0.0)
reg_fcc_label.anchored_position = (6, 22)
reg_fcc_label.hidden = True
main_group.append(reg_fcc_label)

reg_module_label = label.Label(terminalio.FONT, text="Contains cert'd module",
                                color=0xAAAAAA, scale=1)
reg_module_label.anchor_point = (0.0, 0.0)
reg_module_label.anchored_position = (6, 36)
reg_module_label.hidden = True
main_group.append(reg_module_label)

reg_part15_label = label.Label(terminalio.FONT, text="FCC Part 15 compliant",
                                color=0xFFFFFF, scale=1)
reg_part15_label.anchor_point = (0.0, 0.0)
reg_part15_label.anchored_position = (6, 50)
reg_part15_label.hidden = True
main_group.append(reg_part15_label)

reg_rohs_label = label.Label(terminalio.FONT, text="RoHS Compliant",
                              color=0x00D68F, scale=1)
reg_rohs_label.anchor_point = (0.0, 0.0)
reg_rohs_label.anchored_position = (6, 64)
reg_rohs_label.hidden = True
main_group.append(reg_rohs_label)

reg_company_label = label.Label(terminalio.FONT, text="KNOWCO2 LLC",
                                 color=0xFFFFFF, scale=1)
reg_company_label.anchor_point = (0.0, 0.0)
reg_company_label.anchored_position = (6, 78)
reg_company_label.hidden = True
main_group.append(reg_company_label)

reg_url_label = label.Label(terminalio.FONT, text="knowco2.com/compliance",
                             color=0x00B4D8, scale=1)
reg_url_label.anchor_point = (0.0, 0.0)
reg_url_label.anchored_position = (6, 92)
reg_url_label.hidden = True
main_group.append(reg_url_label)

reg_hint_label = label.Label(terminalio.FONT, text="release B to go back",
                              color=0x444444, scale=1)
reg_hint_label.anchor_point = (0.0, 0.0)
reg_hint_label.anchored_position = (6, 118)
reg_hint_label.hidden = True
main_group.append(reg_hint_label)

_REG_LABELS = [reg_title_label, reg_fcc_label, reg_module_label,
               reg_part15_label, reg_rohs_label, reg_company_label,
               reg_url_label, reg_hint_label]

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

def apply_settings():
    global LOW_THRESHOLD, MED_THRESHOLD, ALERT_THRESHOLD
    global alerts_enabled, graph_scale_mode, MAX_POINTS
    global cloud_enabled, cloud_api_url, cloud_device_token, cloud_interval_sec

    LOW_THRESHOLD = int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))
    MED_THRESHOLD = int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))
    ALERT_THRESHOLD = int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))

    alerts_enabled = bool(settings.get("alerts_enabled", True))
    graph_scale_mode = settings.get("graph_scale_mode", "fixed")

    MAX_POINTS = _clamp_int(settings.get("max_points", MAX_POINTS_DEFAULT), 100, 50000, MAX_POINTS_DEFAULT)

    cloud_enabled = bool(settings.get("cloud_enabled", False))
    cloud_api_url = (settings.get("cloud_api_url", "") or "").strip()
    cloud_device_token = (settings.get("cloud_device_token", "") or "").strip()
    cloud_interval_sec = _clamp_int(settings.get("cloud_interval_sec", 60) or 60, 15, 3600, 60)

    # Apply display orientation setting immediately so it takes effect without reboot.
    try:
        import board as _b
        _b.DISPLAY.rotation = 0 if settings.get("display_flip", False) else 180
    except Exception:
        pass

    # Apply color scheme (normal or colorblind-safe).
    try:
        apply_color_scheme()
    except Exception:
        pass

def load_settings():
    global settings, temp_mode, display_mode
    loaded = False
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update(data)
            loaded = True
    except OSError:
        pass
    except ValueError:
        pass

    if not loaded:
        # Primary settings.json is missing or corrupt.  Try the backup that is
        # written alongside every successful save and before every OTA update.
        _bak = SETTINGS_FILE + ".bak"
        try:
            with open(_bak, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                settings.update(data)
                loaded = True
                # Restore the primary file from the backup immediately so
                # subsequent saves/reads work against the normal path.
                try:
                    _ensure_fs_writable()
                    with open(SETTINGS_FILE, "w") as f:
                        json.dump(settings, f)
                except Exception:
                    pass
        except OSError:
            pass
        except ValueError:
            pass

    apply_settings()

    tm = settings.get("temp_mode", "F")
    if tm in ("F", "C"):
        temp_mode = tm

    dm_int = _as_int(settings.get("display_mode", 0), 0)
    if dm_int not in (0, 1, 2):
        dm_int = 0
    display_mode = dm_int

def save_settings():
    global FS_READONLY
    _ensure_fs_writable()
    if FS_READONLY:
        log("save_settings", "settings not saved (filesystem is read-only)", min_interval=10.0)
        return False

    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(settings, f)
        try:
            os.replace(tmp, SETTINGS_FILE)
        except AttributeError:
            os.rename(tmp, SETTINGS_FILE)
        # Keep the backup in sync so it's always a recent copy of good settings.
        try:
            with open(SETTINGS_FILE + ".bak", "w") as _bk:
                json.dump(settings, _bk)
        except Exception:
            pass
        return True
    except OSError as e:
        try:
            if (e.args and e.args[0] == 30) or getattr(e, "errno", None) == 30:
                FS_READONLY = True
        except Exception:
            pass

        _ensure_fs_writable()
        if not FS_READONLY:
            try:
                with open(tmp, "w") as f:
                    json.dump(settings, f)
                try:
                    os.replace(tmp, SETTINGS_FILE)
                except AttributeError:
                    os.rename(tmp, SETTINGS_FILE)
                try:
                    with open(SETTINGS_FILE + ".bak", "w") as _bk:
                        json.dump(settings, _bk)
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

    # Allow the user to set or clear the admin password for the settings page.
    # If provided as an empty string, the password is cleared (disabled).  This
    # password is required via the "pw" query parameter to access the settings UI.
    if "admin_pw" in params:
        # Store the provided password (even if blank).  Do not trim whitespace;
        # spaces are significant.
        settings["admin_password"] = params["admin_pw"] or ""

    if "lang" in params and params["lang"] in ("en", "es", "fr", "de", "pt", "it", "ja", "zh", "ko"):
        settings["lang"] = params["lang"]

    if "ap_ssid" in params and params["ap_ssid"]:
        new_ssid = params["ap_ssid"]
        if new_ssid != old_ap_ssid:
            settings["ap_ssid"] = new_ssid
            ap_changed = True

    if "ap_password" in params and params["ap_password"]:
        new_pass = params["ap_password"]
        if len(new_pass) >= 8 and new_pass != old_ap_pass:
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

    settings["display_flip"] = "display_flip" in params
    settings["colorblind_mode"] = "colorblind_mode" in params
    apply_color_scheme()

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

    # MQTT broker settings
    settings["mqtt_enabled"] = "mqtt_enabled" in params
    if "mqtt_broker" in params:
        settings["mqtt_broker"] = params["mqtt_broker"].strip()
    if "mqtt_port" in params:
        try: settings["mqtt_port"] = int(params["mqtt_port"])
        except Exception: pass
    if "mqtt_user" in params:
        settings["mqtt_user"] = params["mqtt_user"]
    if "mqtt_pass" in params and params["mqtt_pass"]:
        settings["mqtt_pass"] = params["mqtt_pass"]
    if "mqtt_topic_prefix" in params and params["mqtt_topic_prefix"]:
        settings["mqtt_topic_prefix"] = params["mqtt_topic_prefix"].strip()
    if "mqtt_interval_sec" in params:
        try: settings["mqtt_interval_sec"] = max(15, int(params["mqtt_interval_sec"]))
        except Exception: pass

    # Adafruit IO settings
    settings["aio_enabled"] = "aio_enabled" in params
    if "aio_username" in params:
        settings["aio_username"] = params["aio_username"].strip()
    if "aio_key" in params and params["aio_key"]:
        settings["aio_key"] = params["aio_key"]
    if "aio_group_key" in params and params["aio_group_key"]:
        settings["aio_group_key"] = params["aio_group_key"].strip()
    if "aio_interval_sec" in params:
        try: settings["aio_interval_sec"] = max(15, int(params["aio_interval_sec"]))
        except Exception: pass

    # Display dimming schedule
    settings["dim_enabled"] = "dim_enabled" in params
    if "dim_start_hour" in params:
        try: settings["dim_start_hour"] = max(0, min(23, int(params["dim_start_hour"])))
        except Exception: pass
    if "dim_end_hour" in params:
        try: settings["dim_end_hour"] = max(0, min(23, int(params["dim_end_hour"])))
        except Exception: pass
    if "dim_brightness" in params:
        try: settings["dim_brightness"] = max(0, min(100, int(params["dim_brightness"])))
        except Exception: pass

    # Validate and reorder threshold values to ensure they are sensible.  Clamp all
    # thresholds to the range [400, 10000] and enforce an ascending order
    # (low <= med <= alert).  If the user entered values out of order, they
    # will be sorted automatically.  This prevents nonsensical thresholds
    # that could cause weird behavior or crashes.
    try:
        low = int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))
        med = int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))
        alert = int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))
        vals = [low, med, alert]
        # Clamp each value to the allowed range
        vals = [max(400, min(10000, v)) for v in vals]
        # Sort so low <= med <= alert
        vals.sort()
        settings["low_threshold"], settings["med_threshold"], settings["alert_threshold"] = vals
    except Exception:
        pass

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
        if microcontroller is not None and hasattr(microcontroller.cpu, "uid"):
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
            # SCD-30 has no serial_number; use firmware_version as a label instead.
            fw = getattr(scd_obj, "firmware_version", None)
            if fw is not None:
                try:
                    if isinstance(fw, (tuple, list)) and len(fw) >= 2:
                        scd_serial_str = "FW%d.%d" % (fw[0], fw[1])
                    else:
                        scd_serial_str = "FW" + str(fw)
                except Exception:
                    pass
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

scd = None
scd_init_failed = False
sensor_warned = False
try:
    i2c = board.I2C()

    # --- Try SCD-4x (SCD-40 / SCD-41) first ---
    if adafruit_scd4x is not None:
        try:
            scd = adafruit_scd4x.SCD4X(i2c)
            if hasattr(scd, "measure_single_shot"):
                sensor_model_str = "SCD41"
            else:
                sensor_model_str = "SCD40"
            # Serial number must be read BEFORE start_periodic_measurement()
            # because the SCD-4x only allows serial_number in idle state.
            init_scd_serial(scd)
            scd.start_periodic_measurement()
        except Exception as _e4x:
            print("SCD4x init failed:", _e4x)
            scd = None

    # --- Fall back to SCD-30 ---
    if scd is None and adafruit_scd30 is not None:
        try:
            scd = adafruit_scd30.SCD30(i2c)
            sensor_model_str = "SCD30"
            # SCD-30 starts continuous measurement automatically on init;
            # no explicit start call is needed.
            init_scd_serial(scd)
        except Exception as _e30:
            print("SCD30 init failed:", _e30)
            scd = None

    if scd is None:
        raise RuntimeError("No supported CO2 sensor found on I2C bus")
    status_label.text = "Warming up..."
    time.sleep(5)
    status_label.text = ""

    # Initialize the timestamp of the last SCD sample after starting
    # periodic measurement.  This ensures the staleness watchdog starts
    # counting from the moment the sensor begins producing data.
    last_scd_sample_ts = time.monotonic()
except Exception as e:
    scd = None
    scd_init_failed = True
    last_scd_sample_ts = time.monotonic()
    print("SCD init failed:", e)
    try:
        show_status("Sensor init failed")
    except Exception:
        pass

# Apply calibration settings from stored configuration.  This sets the
# Automatic Self Calibration (ASC) state, altitude compensation and
# ambient pressure compensation on the SCD4x.  A value of zero for
# altitude or pressure leaves that compensation disabled.  Any errors
# are ignored to avoid breaking startup if the driver does not support
# a particular property on this build.
try:
    scd.self_calibration_enabled = bool(settings.get("asc_enabled", True))
except Exception:
    pass
try:
    alt_val = settings.get("altitude", 0)
    if alt_val:
        scd.altitude = int(alt_val)
except Exception:
    pass
try:
    ap_val = settings.get("ambient_pressure", 0)
    if ap_val:
        # SCD-4x exposes this as a method; SCD-30 as a settable property.
        if hasattr(scd, "set_ambient_pressure"):
            scd.set_ambient_pressure(int(ap_val))
        else:
            scd.ambient_pressure = int(ap_val)
except Exception:
    pass

# SCD CRC failure counters and recovery for sensor errors.
scd_crc_failures = 0
# Maximum consecutive CRC failures before attempting a sensor reset.
SCD_MAX_FAILS_BEFORE_RESET = 3
# Minimum cooldown between sensor resets (seconds) to avoid rapid resets.
SCD_RESET_COOLDOWN_SEC = 2.0
# Timestamp of the last sensor reset.
last_scd_reset = 0.0
# Number of consecutive SCD recoveries since the last good sample.
scd_recoveries = 0
# Max consecutive soft-recovery attempts before escalating to an MCU reset.
SCD_MAX_RECOVERIES_BEFORE_RESET = 3

# Seconds without a new reading before triggering soft recovery (long enough
# to avoid false triggers during brief WiFi/NTP delays).
SCD_SAMPLE_TIMEOUT = 30.0

# Timestamp of the last successful SCD4x sample (monotonic time).
last_scd_sample_ts = 0.0

# ----------------------------------------------------------------------
# Calibration parameter limits
#
# To prevent invalid values from being written to the SCD4x sensor,
# user-entered altitude and ambient pressure values are clamped to the
# ranges defined below.  Altitude is measured in meters above sea
# level and can be set from 0 (disabled) up to 10 000 m.  Ambient
# pressure is measured in hectopascals (hPa).  A value of 0 disables
# pressure compensation; otherwise values are clamped between 400 and
# 2000 hPa.
ALTITUDE_MIN = 0
ALTITUDE_MAX = 10000
PRESSURE_MIN_NONZERO = 400
PRESSURE_MAX = 2000

def scd_recover():
    """Attempt to recover the SCD4x sensor after repeated CRC failures."""
    global scd_crc_failures, last_scd_reset, scd, scd_recoveries
    if scd is None:
        return
    now = time.monotonic()

    # Feed hardware watchdog early each loop.
    if _wd is not None:
        try:
            _wd.feed()
        except Exception:
            pass
    # Avoid resetting too frequently
    if (now - last_scd_reset) < SCD_RESET_COOLDOWN_SEC:
        return
    last_scd_reset = now
    scd_crc_failures = 0
    scd_recoveries += 1
    try:
        # Stop measurement (API differs between sensor families)
        try:
            if hasattr(scd, "stop_periodic_measurement"):
                scd.stop_periodic_measurement()       # SCD-4x
            elif hasattr(scd, "stop_continuous_measurements"):
                scd.stop_continuous_measurements()    # SCD-30
            time.sleep(0.2)
        except Exception:
            pass
        # Soft-reset the sensor
        try:
            if hasattr(scd, "soft_reset"):
                scd.soft_reset()                      # SCD-4x
            elif hasattr(scd, "reset"):
                scd.reset()                           # SCD-30
            time.sleep(0.8)
        except Exception:
            pass
        # Restart measurement (SCD-30 resumes automatically after reset)
        try:
            if hasattr(scd, "start_periodic_measurement"):
                # Re-enter the correct measurement mode.  If LP mode is
                # active and the sensor supports it, restart in LP mode
                # so a watchdog recovery never silently exits LP mode.
                _lp_ok = (energy_mode and sensor_model_str == "SCD41"
                          and hasattr(scd, "start_low_power_periodic_measurement"))
                if _lp_ok:
                    scd.start_low_power_periodic_measurement()
                else:
                    scd.start_periodic_measurement()
            time.sleep(0.2)
        except Exception:
            pass
        # Indicate success on the display
        show_status("SCD: recovered")
    except Exception as e:
        # Indicate failure and log
        show_status("SCD: reset fail")
        log("scd_reset", "SCD recover failed:", e, min_interval=2.0)

    if scd_recoveries >= SCD_MAX_RECOVERIES_BEFORE_RESET:
        log("scd_reset", "SCD recoveries exceeded; MCU reset", scd_recoveries, min_interval=2.0)
        show_status("SCD: restarting")
        time.sleep(0.5)
        try:
            if microcontroller is not None:
                microcontroller.reset()
        except Exception:
            pass
        scd_recoveries = 0



# ----------------------------------------------------------------------
# Calibration helpers
# ----------------------------------------------------------------------

def perform_force_calibration(ref_ppm):
    """
    Perform a forced calibration of the SCD4x sensor against a known CO₂
    concentration.  On success, updates the last calibration timestamp and
    reference value in the settings and persists them to storage.  The
    reference value should be an integer ppm (typically around 400–500 ppm
    when calibrating outdoors).  Returns True on success, False on failure.
    """
    global settings
    if scd is None:
        show_status("Calibration failed")
        return False
    try:
        # Ensure ref_ppm is an integer and within a reasonable range
        target = int(ref_ppm)
        if target < 300 or target > 10000:
            return False
    except Exception:
        return False
    try:
        # Attempt the calibration.  The driver call may return a baseline
        # or raise an exception on error.  We ignore any return value.
        scd.force_calibration(target)
        # Record timestamp and reference value.  Use time.time() if real
        # time is available; fall back to monotonic as best effort.
        try:
            ts = time.time()
        except Exception:
            ts = time.monotonic()
        settings["last_calibration_ts"] = ts
        settings["last_calibration_ref"] = target
        save_settings()
        show_status(f"Calibrated to {target} ppm")
        return True
    except Exception as e:
        log("calibration", "force_calibration error:", e, min_interval=2.0)
        show_status("Calibration failed")
        return False

# ======================================================================
#  UI LOGIC
# ======================================================================

def color_for_co2(co2):
    if co2 < LOW_THRESHOLD:
        return _active_scheme["low"]
    elif co2 < MED_THRESHOLD:
        return _active_scheme["med"]
    else:
        return _active_scheme["alert"]

def graph_color_index_for_co2(val):
    if val < LOW_THRESHOLD:
        return 2
    elif val < MED_THRESHOLD:
        return 3
    else:
        return 4

def apply_color_scheme():
    """Apply the active color scheme to all live display elements.

    Call this after changing settings["colorblind_mode"] and whenever
    the device first starts.  Safe to call at any time.
    """
    global _active_scheme
    cb = settings.get("colorblind_mode", False)
    _active_scheme = _SCHEME_CB if cb else _SCHEME_NORMAL
    # Update the graph bitmap palette (affects all bars immediately on refresh)
    graph_palette[2] = _active_scheme["low"]
    graph_palette[3] = _active_scheme["med"]
    graph_palette[4] = _active_scheme["alert"]
    # Update threshold line label colors
    try:
        low_label.color  = _active_scheme["low"]
        med_label.color  = _active_scheme["med"]
        high_label.color = _active_scheme["alert"]
    except Exception:
        pass
    # Update graph time-axis label colors.
    # Normal mode: brand teal contrasts well against yellow bars.
    # Colorblind mode: white — no conflict with the blue/amber/vermillion palette.
    _axis_label_color = 0xFFFFFF if cb else 0x00B4D8
    try:
        x_right_label.color = _axis_label_color
        x_mid_label.color   = _axis_label_color
    except Exception:
        pass
    # Refresh the live CO2 display color immediately
    try:
        if last_co2 is not None:
            apply_alert_colors(last_co2)
    except Exception:
        pass

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
    global last_co2, last_temp_c, last_rh, rate_of_change

    if screen != SCREEN_MAIN:
        return

    if display_mode == 2:
        # Show the most recent CO₂ value along with the trend arrow and
        # the instantaneous rate of change (ppm per second) if available.
        if last_co2 is not None:
            arrow = compute_trend_arrow()
            if rate_of_change is not None:
                # Show a sign (+/-) and one decimal place for the rate.  Use ppm/s units.
                graph_value_label.text = "%d ppm %s %+.1f ppm/s%s" % (int(last_co2), arrow, rate_of_change, " [LP]" if energy_mode else "")
            else:
                graph_value_label.text = "%d ppm %s%s" % (int(last_co2), arrow, " [LP]" if energy_mode else "")
        else:
            graph_value_label.text = "-- ppm"
    else:
        # When not in graph-only mode, hide the graph value label text.
        graph_value_label.text = ""

    if last_co2 is None or last_temp_c is None or last_rh is None:
        if display_mode in (0, 1):
            co2_label.text = "CO2: ---- ppm"
            co2_label.scale = 3
            ppm_label.hidden = True
            if display_mode == 0:
                th_label.text = "--.-F  --.-%"
        return

    co2 = last_co2
    t_c = last_temp_c
    rh = last_rh
    t_f = t_c * 9 / 5 + 32

    if display_mode == 1:
        # Big CO2 mode: number only, no units, maximum readable scale.
        # terminalio.FONT is 6 px wide per character at scale=1.
        # Scales chosen so the widest expected value (9999 = 4 chars) still fits
        # the 240 px display with a small margin, and 5 digits (10000) still fits.
        co2_str = "%d" % int(co2)
        ndigits = len(co2_str)
        if ndigits <= 2:
            big_scale = 16   # 2 chars × 6 × 16 = 192 px wide
        elif ndigits == 3:
            big_scale = 12   # 3 chars × 6 × 12 = 216 px wide
        elif ndigits == 4:
            big_scale = 9    # 4 chars × 6 × 9  = 216 px wide
        else:
            big_scale = 7    # 5 chars × 6 × 7  = 210 px wide
        co2_label.scale = big_scale
        co2_label.text = co2_str
        co2_label.anchored_position = (display.width // 2, display.height // 2)
        ppm_label.hidden = True
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
    """Create/refresh the QR code(s) on the AP info screen.

    In STA mode: always shows the URL QR (single page, full size).
    In AP mode: shows one QR at a time, toggled by _qr_page:
      page 0 -> WiFi join QR  ("Scan to join"  +  "1/2")
      page 1 -> URL / IP QR   ("Open page"     +  "2/2")

    Rebuild only when payload or page changes to avoid flicker.
    """
    global qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2, qr_page_indicator
    global _last_wifi_payload, _last_url_payload, _last_qr_target_modules, _last_qr_scale, _last_qr_right_x
    global _last_qr_page

    if adafruit_miniqr is None:
        return

    try:
        # Prefer mDNS URL when in STA (friendly + stable), else use IP.
        if wifi_mode == WIFI_MODE_STA and mdns_hostname:
            url_payload = "http://%s.local/" % mdns_hostname
        else:
            url_payload = build_url_qr_payload(ip_str)

        wifi_payload = build_wifi_qr_payload(ssid, pw)

        # If nothing changed (including page), do nothing.
        if (wifi_payload == _last_wifi_payload and url_payload == _last_url_payload
                and _qr_page == _last_qr_page):
            return

        # Remove old QR objects (if any)
        for obj in (qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2, qr_page_indicator):
            if obj is not None:
                try:
                    main_group.remove(obj)
                except Exception:
                    pass

        qr_tilegrid_wifi = None
        qr_tilegrid_url = None
        qr_caption1 = None
        qr_caption2 = None
        qr_page_indicator = None

        margin = 2
        avail_h = display.height - (2 * margin)
        caption_h = 12  # terminalio font cell height
        cap_pad = 2     # gap between caption bottom and QR top

        # Measure module counts for both QRs.
        tmp = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
        tmp.add_data(wifi_payload); tmp.make()
        modules_wifi = tmp.matrix.width

        tmp2 = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
        tmp2.add_data(url_payload); tmp2.make()
        modules_url = tmp2.matrix.width

        # Choose which QR to show and what caption to use.
        if wifi_mode == WIFI_MODE_STA:
            # STA: always show URL QR, no page indicator needed.
            target_modules = modules_url
            payload = url_payload
            caption1_text = "Open page"
            show_indicator = False
        else:
            # AP: page-toggle between WiFi QR (page 0) and URL QR (page 1).
            if _qr_page == 0:
                target_modules = modules_wifi
                payload = wifi_payload
                caption1_text = "Scan to join"
            else:
                target_modules = modules_url
                payload = url_payload
                caption1_text = "Open page"
            show_indicator = True

        scale = 2
        for _s in (4, 3, 2):
            if (caption_h + cap_pad + target_modules * _s) <= avail_h:
                scale = _s
                break
        size = target_modules * scale
        top_y = margin + max(0, (avail_h - caption_h - cap_pad - size) // 2)
        cap1_y = top_y
        qr1_y  = top_y + caption_h + cap_pad

        # Right-align the QR block.
        cap_w = len(caption1_text) * 6
        right_x = display.width - margin - max(size, cap_w) - margin
        if right_x < 2:
            right_x = 2

        # Build the TileGrid.
        if wifi_mode == WIFI_MODE_STA or _qr_page == 1:
            qr_tilegrid_url = _make_qr_tile(payload, right_x, qr1_y,
                                            scale=scale, target_modules=target_modules)
            if qr_tilegrid_url is not None:
                main_group.append(qr_tilegrid_url)
        else:
            qr_tilegrid_wifi = _make_qr_tile(payload, right_x, qr1_y,
                                             scale=scale, target_modules=target_modules)
            if qr_tilegrid_wifi is not None:
                main_group.append(qr_tilegrid_wifi)

        qr_caption1 = label.Label(terminalio.FONT, text=caption1_text, color=0xAAAAAA, scale=1)
        qr_caption1.anchor_point = (0.0, 0.0)
        qr_caption1.anchored_position = (right_x, cap1_y)
        main_group.append(qr_caption1)

        # Page indicator "1/2" or "2/2" bottom-right, dimmed.
        if show_indicator:
            ind_text = "1/2" if _qr_page == 0 else "2/2"
            qr_page_indicator = label.Label(terminalio.FONT, text=ind_text, color=0x555555, scale=1)
            qr_page_indicator.anchor_point = (1.0, 1.0)
            qr_page_indicator.anchored_position = (display.width - margin, display.height - margin)
            main_group.append(qr_page_indicator)

        _last_wifi_payload = wifi_payload
        _last_url_payload = url_payload
        _last_qr_target_modules = target_modules
        _last_qr_scale = scale
        _last_qr_right_x = right_x
        _last_qr_page = _qr_page
    except Exception as e:
        _last_wifi_payload = None
        _last_url_payload = None
        _last_qr_page = None
        log("qr", "QR update failed:", e, min_interval=2.0)

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
    ap_scd_label.text = sensor_model_str + ": " + (scd_short or "N/A")
    ap_fw_label.text = "FW:" + FIRMWARE_VERSION + "  CP:" + cp_version_str

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
    """Update the TWC top-right status indicator letters.

    T = NTP-synced STA connection (teal when active, dim otherwise)
    W = WiFi connected in STA mode (teal when active, dim otherwise)
    C = Cloud upload succeeded recently (teal when active, dim otherwise)

    In AP mode while on the info/QR screen the QR captions start near y=2,
    so all three letters are dimmed to avoid overlap.
    """
    try:
        sta_connected = (wifi is not None and wifi_mode == WIFI_MODE_STA
                         and wifi.radio.connected)
    except Exception:
        sta_connected = False

    try:
        _now = time.monotonic()
        _cloud_ok_recent = (cloud_last_ok > 0.0) and ((_now - cloud_last_ok) <= CLOUD_OK_TTL)
        cloud_active = cloud_enabled and wifi_mode == WIFI_MODE_STA and _cloud_ok_recent
    except Exception:
        cloud_active = False

    # Dim all letters when on AP-mode info screen to avoid QR caption overlap.
    if screen == SCREEN_APINFO and wifi_mode == WIFI_MODE_AP:
        twc_t_label.color = _TWC_DIM
        twc_w_label.color = _TWC_DIM
        twc_c_label.color = _TWC_DIM
        return

    twc_t_label.color = _TWC_ACTIVE if (sta_connected and ntp_synced) else _TWC_DIM
    twc_w_label.color = _TWC_ACTIVE if sta_connected else _TWC_DIM
    twc_c_label.color = _TWC_ACTIVE if cloud_active else _TWC_DIM

    # Keep legacy placeholder labels silent.
    try:
        wifi_ind_label.text  = ""
        cloud_ind_label.text = ""
    except Exception:
        pass
def update_visibility():
    main_visible = (screen == SCREEN_MAIN)
    ap_visible = (screen == SCREEN_APINFO)

    # Sensor frozen banner only appears on the main screen.
    sensor_frozen_label.hidden = not (main_visible and sensor_frozen_shown)

    # LP badge shown on all screens except graph mode (where [LP] is appended
    # to graph_value_label text instead). New top-right position clears all
    # other labels on both SCREEN_MAIN and SCREEN_APINFO.
    try:
        lp_badge_label.hidden = not energy_mode or show_graph
    except Exception:
        pass
    # Battery warning only on main screen (managed in batt-refresh block too).
    try:
        _bv = (fuel_gauge is not None and cached_pct is not None
               and cached_pct < BATT_WARN_PCT)
        batt_warn_label.hidden = not (main_visible and _bv)
    except Exception:
        pass

    th_label.hidden = not main_visible

    show_graph = main_visible and (display_mode == 2)
    graph.hidden = not show_graph
    y_min_label.hidden = not show_graph
    y_max_label.hidden = not show_graph
    x_left_label.hidden = not show_graph
    x_right_label.hidden = not show_graph
    x_mid_label.hidden = not show_graph
    low_label.hidden = not show_graph
    med_label.hidden = not show_graph
    high_label.hidden = not show_graph
    graph_value_label.hidden = not show_graph

    # In graph mode the co2_label must be explicitly hidden — it is still
    # "main_visible" so the generic hide above does not catch it, and a
    # large scale (e.g. 12) left over from big-CO2 mode would bleed into
    # the graph area as a ghost line.
    co2_label.hidden = not main_visible or (display_mode == 2)

    if main_visible:
        if display_mode == 0:
            co2_label.scale = 3
            # Restore the position that big-CO2 mode may have overwritten.
            co2_label.anchored_position = (display.width // 2, display.height // 2 - 22)
            th_label.hidden = False
        elif display_mode == 1:
            # Scale and position are set dynamically in refresh_text().
            th_label.hidden = True
    ppm_label.hidden = True  # never shown; kept in group for future use

    ap_ssid_label.hidden = not ap_visible
    ap_pass_label.hidden = not ap_visible
    ap_ip_label.hidden = not ap_visible
    ap_batt_label.hidden = not ap_visible
    ap_hw_label.hidden = not ap_visible
    ap_scd_label.hidden = not ap_visible
    ap_fw_label.hidden = not ap_visible

    for _obj in (qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2, qr_page_indicator):
        if _obj is not None:
            _obj.hidden = not ap_visible

    # Regulatory screen labels — only visible on SCREEN_REGULATORY.
    # Also ensure APINFO labels are hidden when regulatory screen is active.
    reg_visible = (screen == SCREEN_REGULATORY)
    for _rl in _REG_LABELS:
        _rl.hidden = not reg_visible
    if reg_visible:
        ap_ssid_label.hidden = True
        ap_pass_label.hidden = True
        ap_ip_label.hidden = True
        ap_batt_label.hidden = True
        ap_hw_label.hidden = True
        ap_scd_label.hidden = True
        ap_fw_label.hidden = True
        for _obj in (qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2, qr_page_indicator):
            if _obj is not None:
                _obj.hidden = True

update_visibility()

def update_axis_labels(low, high, span_seconds):
    # Y-axis max scale value (top of graph) — the only dynamic label now.
    y_max_label.text = str(int(high))
    # All other axis labels are static:
    #   y_min_label  = "t-5.0m"  (bottom-left  X-axis anchor)
    #   x_mid_label  = "-2.5m"   (bottom-centre X-axis midpoint)
    #   x_right_label= "now"     (bottom-right  X-axis anchor)
    #   x_left_label = "CO2 ppm" (top-left      Y-axis label)
    # Nothing to update for those.

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

    low_label.text = str(int(LOW_THRESHOLD))
    med_label.text = str(int(MED_THRESHOLD))
    high_label.text = str(int(ALERT_THRESHOLD))

def redraw_graph():
    global graph_drawing
    # If a redraw is already underway, skip this call to avoid stalling the UI.
    if graph_drawing:
        return
    graph_drawing = True
    try:
        graph_bitmap.fill(0)
        # If there is no CO2 history yet, just clear the axis labels.
        if not co2_history:
            x_left_label.text = ""
            x_right_label.text = ""
        else:
            # Build a view into the most recent data points.
            n_total = len(co2_history)
            start_index = max(0, n_total - WINDOW_SAMPLES)
            visible = co2_history[start_index:]
            n = len(visible)

            span_seconds = min(WINDOW_SECONDS, max(0, (n - 1) * SCD_MEASUREMENT_PERIOD))

            # Determine auto-scaling for the graph.
            if graph_scale_mode == "fixed":
                low, high = 400, 2000
            elif graph_scale_mode == "wide":
                low, high = 400, 3000
            else:
                low = max(400, min(visible))
                high = max(800, max(visible))

            span = max(1, high - low)

            # Divide by (WINDOW_SAMPLES-1) so a full buffer of 61 samples
            # at 4 px each = 240 px, filling the display edge-to-edge.
            # Proportional bar placement: oldest bar starts at x=2 (just right of
            # the Y-axis line), newest bar ends at GRAPH_WIDTH-1 (right edge).
            # This guarantees no gap at the left regardless of PIXELS_PER_SAMPLE.
            _total_px = GRAPH_WIDTH - 2  # drawable columns excluding Y-axis line

            # Horizontal grid lines at 25 %, 50 % and 75 % of graph height.
            for y in [int(GRAPH_HEIGHT * 0.25), int(GRAPH_HEIGHT * 0.5), int(GRAPH_HEIGHT * 0.75)]:
                if 0 <= y < GRAPH_HEIGHT:
                    for x in range(GRAPH_WIDTH):
                        graph_bitmap[x, y] = 1

            # Vertical grid lines every 20 px from the Y-axis.
            for x in range(2, GRAPH_WIDTH, 20):
                for yy in range(GRAPH_HEIGHT):
                    if graph_bitmap[x, yy] == 0:
                        graph_bitmap[x, yy] = 1

            latest_x = GRAPH_WIDTH - 1
            latest_y = None

            for k in range(n):
                if k % 10 == 0:
                    _poll_buttons_during_block()
                val = max(low, min(visible[k], high))
                frac = (val - low) / span
                h = int(frac * (GRAPH_HEIGHT - 1))
                color_idx = graph_color_index_for_co2(val)

                # Map sample index to pixel column proportionally.
                x_start = 2 + int(k * _total_px / max(n, 1))
                x_end   = min(2 + int((k + 1) * _total_px / max(n, 1)) - 1,
                              GRAPH_WIDTH - 1)
                if x_start > x_end:
                    x_end = x_start

                for x in range(x_start, x_end + 1):
                    for yy in range(GRAPH_HEIGHT - 1, GRAPH_HEIGHT - 1 - h, -1):
                        graph_bitmap[x, yy] = color_idx

                if k == n - 1:
                    latest_x = x_end
                    latest_y = GRAPH_HEIGHT - 1 - h

            # White dot on the most recent point.
            if latest_y is not None:
                for dy in (-1, 0, 1):
                    yy = latest_y + dy
                    if 0 <= yy < GRAPH_HEIGHT:
                        graph_bitmap[latest_x, yy] = 5

            # "now" and midpoint labels have fixed positions — no repositioning needed.

            # Update the threshold labels and Y-axis scale label.
            _set_threshold_label_positions(low, high)
            update_axis_labels(low, high, span_seconds)

            # ── Axis border lines (drawn last so they sit over all bars) ──
            # Y-axis: 2-pixel wide vertical line at the left edge of the graph bitmap.
            # On screen this appears at x = GRAPH_MARGIN, forming a clear border
            # between the label gutter and the plotted area.
            for yy in range(GRAPH_HEIGHT):
                graph_bitmap[0, yy] = 6
                if GRAPH_WIDTH > 1:
                    graph_bitmap[1, yy] = 6
            # X-axis: 2-pixel tall horizontal line at the very bottom of the bitmap.
            for xx in range(GRAPH_WIDTH):
                graph_bitmap[xx, GRAPH_HEIGHT - 1] = 6
                if GRAPH_HEIGHT > 1:
                    graph_bitmap[xx, GRAPH_HEIGHT - 2] = 6
    finally:
        # Mark redraw complete so future redraw requests may proceed
        graph_drawing = False

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
        except Exception as e:
            err = e.args[0] if e.args else None
            if err == 11:  # EAGAIN / EWOULDBLOCK
                time.sleep(0.01)
                continue
            log("send_err", "send_all error:", e, min_interval=1.0)
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
        "Access-Control-Allow-Origin: *\r\n" +
        "X-Content-Type-Options: nosniff\r\n" +
        "X-Frame-Options: SAMEORIGIN\r\n" +
        "Referrer-Policy: no-referrer\r\n"
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
        except Exception:
            time.sleep(0.01)
    return data

def _read_request_body(conn, headers_raw, max_bytes=8192, max_wait=3.0):
    """Read the POST body from conn.  headers_raw is the raw bytes of the request
    head (already read).  Returns body as bytes or b'' on error."""
    try:
        content_length = 0
        for line in headers_raw.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except Exception:
                    pass
                break
        if content_length <= 0:
            return b""
        content_length = min(content_length, max_bytes)
        body = b""
        start = time.monotonic()
        while len(body) < content_length and (time.monotonic() - start) < max_wait:
            try:
                chunk = sock_recv(conn, min(512, content_length - len(body)))
                if not chunk:
                    break
                body += chunk
            except Exception:
                time.sleep(0.01)
        return body
    except Exception:
        return b""

def _stream_request_body_to_file(conn, headers_raw, dest_path, max_bytes=400000, max_wait=300.0):
    """Stream a POST body directly to a file in 512-byte chunks without buffering in RAM.
    Returns (success: bool, message: str).

    IMPORTANT: _read_request_head reads in 512-byte chunks and stops after finding
    \\r\\n\\r\\n, but the chunk containing the separator may include bytes PAST the
    separator that are the beginning of the request body.  We split headers_raw at
    the first \\r\\n\\r\\n, treat everything before it as headers and everything after
    as already-received body bytes that must be written first.
    """
    try:
        # Split headers from any body bytes that were already consumed by _read_request_head.
        sep = headers_raw.find(b"\r\n\r\n")
        if sep >= 0:
            headers_only = headers_raw[:sep]
            body_prefix = headers_raw[sep + 4:]   # bytes after the blank line
        else:
            headers_only = headers_raw
            body_prefix = b""

        content_length = 0
        for line in headers_only.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except Exception:
                    pass
                break
        if content_length <= 0:
            return False, "Missing Content-Length header"
        if content_length > max_bytes:
            return False, "File too large (%d bytes, max %d)" % (content_length, max_bytes)

        # Extend the hardware watchdog timeout for the duration of the file write.
        # The normal 20 s timeout fires during large uploads because the main loop
        # (which feeds the watchdog) is blocked.  A mid-write reset leaves a partial
        # file and causes CircuitPython to reformat the filesystem on the next boot.
        try:
            if _wd is not None:
                _wd.timeout = 90
        except Exception:
            pass

        # Increase per-recv timeout so brief WiFi gaps (e.g. TCP window updates)
        # don't abort the upload.  The overall max_wait timer still caps total time.
        try:
            conn.settimeout(30)
        except Exception:
            pass

        written = 0
        _empty_streak = 0
        start = time.monotonic()
        with open(dest_path, "wb") as f:
            # Write any bytes already read past the header separator first.
            if body_prefix:
                f.write(body_prefix)
                written += len(body_prefix)
            # Stream the remainder from the socket.
            while written < content_length:
                if (time.monotonic() - start) > max_wait:
                    return False, "Upload timed out after %d bytes" % written
                remaining = content_length - written
                # --- recv ---
                try:
                    chunk = sock_recv(conn, min(512, remaining))
                except Exception:
                    # Transient recv error (e.g. EAGAIN, brief timeout) — retry.
                    # The overall max_wait clock above handles truly dead connections.
                    try:
                        if _wd is not None:
                            _wd.feed()
                    except Exception:
                        pass
                    time.sleep(0.05)
                    continue
                if not chunk:
                    _empty_streak += 1
                    if _empty_streak > 200:  # ~2 s of empty reads → socket closed
                        return False, "Connection closed after %d of %d bytes" % (written, content_length)
                    try:
                        if _wd is not None:
                            _wd.feed()
                    except Exception:
                        pass
                    time.sleep(0.01)
                    continue
                # --- write ---
                _empty_streak = 0
                try:
                    f.write(chunk)
                except Exception as _we:
                    return False, "Disk write error after %d bytes: %s" % (written, str(_we))
                # Feed the watchdog on every chunk so a long write never
                # triggers a hardware reset mid-file.
                try:
                    if _wd is not None:
                        _wd.feed()
                except Exception:
                    pass
                written += len(chunk)
        if written < content_length:
            return False, "Incomplete upload: %d of %d bytes received" % (written, content_length)
        return True, "OK"
    except Exception as e:
        return False, "Stream error: " + str(e)

_CAPTIVE_PATHS_204 = {
    "/generate_204", "/gen_204", "/ncsi.txt", "/connecttest.txt", "/success.txt", "/hotspot-detect.html",
    "/canonical.html", "/mobile/status.php", "/library/test/success.html", "/fwlink", "/fwlink/", "/redirect",
}

def render_settings_page():
    # Collect garbage before building the ~35 KB settings HTML string.
    # Without this the string concatenation can fragment the heap enough
    # to trigger a MemoryError or, on some CP builds, a hard fault.
    gc.collect()
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
    # Embed colorblind_mode so the canvas chart uses the same palette as the device.
    web_cb_mode = "true" if settings.get("colorblind_mode", False) else "false"

    cloud_enabled_checked = "checked" if settings.get("cloud_enabled", False) else ""
    cloud_api = settings.get("cloud_api_url", "")
    # If no cloud API URL is stored yet, prefill with the default knowco2 API endpoint.
    if not cloud_api:
        cloud_api = "https://api.knowco2.com"

    def esc_attr(val):
        try:
            return str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        except Exception:
            return ""

    # MQTT section
    mqtt_enabled = settings.get("mqtt_enabled", False)
    mqtt_broker = esc_attr(str(settings.get("mqtt_broker", "") or ""))
    mqtt_port = esc_attr(str(settings.get("mqtt_port", 1883) or 1883))
    mqtt_user = esc_attr(str(settings.get("mqtt_user", "") or ""))
    mqtt_topic_prefix = esc_attr(str(settings.get("mqtt_topic_prefix", "knowco2") or "knowco2"))
    mqtt_interval = esc_attr(str(settings.get("mqtt_interval_sec", 60) or 60))
    mqtt_checked = "checked" if mqtt_enabled else ""

    aio_enabled = settings.get("aio_enabled", False)
    aio_username = esc_attr(str(settings.get("aio_username", "") or ""))
    aio_group = esc_attr(str(settings.get("aio_group_key", "knowco2") or "knowco2"))
    aio_interval = esc_attr(str(settings.get("aio_interval_sec", 60) or 60))
    aio_checked = "checked" if aio_enabled else ""

    dim_enabled = settings.get("dim_enabled", False)
    dim_start = esc_attr(str(settings.get("dim_start_hour", 22) or 22))
    dim_end = esc_attr(str(settings.get("dim_end_hour", 7) or 7))
    dim_brightness = esc_attr(str(settings.get("dim_brightness", 10) or 10))
    dim_checked = "checked" if dim_enabled else ""

    current_lang = settings.get("lang", "en")

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

    # Build the settings page HTML.  If an admin password is configured, include it as a
    # hidden field named "pw" so that the password is preserved across form submissions.
    pw_hidden_field = ""
    try:
        _admin_pw = settings.get("admin_password", "")
        if _admin_pw:
            # Always HTML-escape the password value for safety.  We avoid importing
            # urllib here by manually replacing special characters that could break
            # the attribute.  The password should not contain quotes because the
            # input field for admin_pw is of type password.
            esc_pw = _admin_pw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # Embed the escaped password directly into the value attribute.  Use double
            # quotes around the value to avoid breaking the surrounding HTML.
            pw_hidden_field = "<input type=\"hidden\" name=\"pw\" value=\"" + esc_pw + "\">\n"
    except Exception:
        pw_hidden_field = ""

    html = """<!DOCTYPE html>
<html lang=\"""" + current_lang + """\">
<head>
  <meta charset="utf-8">
  <title>Know CO2 Settings</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    *,*::before,*::after{box-sizing:border-box}
    :root{--accent:#00bcd4;--accent-dark:#0097a7;--bg:#0b0b0b;--surface:#111;--border:#333;--text:#eee;--muted:#888;--danger:#e53935;--warn:#ffb300;--green:#4caf50;--radius:6px;--focus-ring:3px solid #00bcd4}
    html{font-size:16px;line-height:1.5}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);margin:0;padding:0}
    .skip-link{position:absolute;top:-40px;left:0;background:var(--accent);color:#000;padding:8px 16px;text-decoration:none;border-radius:0 0 var(--radius) 0;font-weight:600;z-index:999}
    .skip-link:focus{top:0}
    .wrap{max-width:640px;margin:0 auto;padding:16px}
    h1{color:var(--accent);margin:8px 0 4px;font-size:1.5rem}
    h2{font-size:1rem;color:var(--muted);margin:0 0 12px;font-weight:400}
    fieldset{border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;margin:12px 0}
    legend{color:var(--accent);font-weight:600;font-size:0.9rem;padding:0 6px;text-transform:uppercase;letter-spacing:0.04em}
    label{display:block;margin-top:12px;font-size:0.95rem;color:var(--text)}
    label:first-child{margin-top:0}
    label span.lbl{display:block;margin-bottom:4px;font-weight:500}
    input[type=text],input[type=password],input[type=number],input[type=url],input[type=email],select,textarea{width:100%;padding:10px 12px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);color:var(--text);font-size:1rem;font-family:inherit;transition:border-color .15s,box-shadow .15s;min-height:44px}
    input[type=text]:focus,input[type=password]:focus,input[type=number]:focus,input[type=url]:focus,input[type=email]:focus,select:focus,textarea:focus{outline:var(--focus-ring);outline-offset:2px;border-color:var(--accent)}
    input[type=checkbox]{width:18px;height:18px;accent-color:var(--accent);cursor:pointer;flex-shrink:0;margin-right:8px}
    .check-label{display:flex;align-items:flex-start;gap:8px;cursor:pointer;padding:4px 0;min-height:44px;align-items:center}
    .check-label input[type=checkbox]{margin-top:0}
    .help-text{font-size:0.8rem;color:var(--muted);margin-top:4px;line-height:1.4}
    button[type=submit],.btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;padding:10px 24px;border-radius:var(--radius);border:none;font-size:1rem;font-weight:600;cursor:pointer;transition:background .15s,transform .1s;letter-spacing:0.01em}
    button[type=submit]:focus,.btn:focus{outline:var(--focus-ring);outline-offset:2px}
    button[type=submit]:active,.btn:active{transform:scale(0.98)}
    .btn-primary{background:var(--accent);color:#000}
    .btn-primary:hover{background:var(--accent-dark)}
    .btn-danger{background:var(--danger);color:#fff}
    .btn-danger:hover{background:#c62828}
    .btn-block{width:100%;margin-top:16px}
    .code{font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;font-size:0.85rem;background:#1a1a1a;padding:2px 6px;border-radius:3px}
    .muted{color:var(--muted)}
    .warn-text{color:var(--warn)}
    .api-note{font-size:0.8rem;color:var(--muted);margin-top:8px;padding:8px;background:#1a1a1a;border-radius:var(--radius);border-left:3px solid var(--border)}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    a:focus{outline:var(--focus-ring);outline-offset:2px;border-radius:2px}
    .row{display:flex;gap:12px;flex-wrap:wrap}
    .row label{flex:1;min-width:140px}
    nav.page-nav{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;padding-top:12px;border-top:1px solid var(--border)}
    nav.page-nav a{font-size:0.9rem;padding:6px 12px;border-radius:var(--radius);border:1px solid var(--border);color:var(--muted)}
    nav.page-nav a:hover{color:var(--text);border-color:var(--muted)}
    .version-badge{font-size:0.75rem;font-weight:400;color:#555;vertical-align:middle;margin-left:4px}
    #chart-container{margin-top:12px;border:1px solid var(--border);border-radius:var(--radius);padding:8px}
    #chart{width:100%;max-width:420px;height:140px;background:#050505;border-radius:4px}
    #chart-debug{font-size:10px;color:#888;margin-top:4px}
    #status-card{border:1px solid var(--border);border-radius:var(--radius);padding:10px;margin:10px 0;background:var(--surface)}
    #status-main{font-size:18px;margin-bottom:6px}
    #status-extra{font-size:12px;color:#ccc}
    .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;border:1px solid #444;margin-right:4px}
    .badge-low{border-color:#00c853;color:#00e676}
    .badge-med{border-color:#ffeb3b;color:#fff176}
    .badge-high{border-color:#ff5252;color:#ff8a80}
    @media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
    @media(max-width:480px){.wrap{padding:12px}.row{flex-direction:column}}
  </style>
</head>
<body>
  <a href="#main-content" class="skip-link" data-i18n="skip_nav">Skip to main content</a>
  <div class="wrap" id="main-content" role="main">
    <h1>Know CO2 <span class="version-badge">""" + FIRMWARE_VERSION + """</span></h1>
    <div class="muted" style="font-size:12px;margin-bottom:10px;">
      Open <span class="code">http://""" + ip_for_hint + """/</span>.""" + mdns_hint + """
      <br><small class="muted">If your phone says "No Internet", that’s expected during AP setup.</small>
    </div>

    <div id="status-card">
      <div id="status-main">CO₂: <span id="status-co2">--</span> ppm <span id="status-arrow" title="Trend (↑ rising, ↓ falling, → steady)">-</span> <span id="status-rate"></span></div>
      <div id="status-extra">
        Temp: <span id="status-temp">--.-</span>°
        &nbsp;·&nbsp; RH: <span id="status-rh">--.-</span>%
        <br>
        Air quality: <span id="status-quality" class="badge">unknown</span>
        <br>
        Device ID: <code id="status-device-id">""" + device_id + """</code>
        <div style="margin-top:8px;font-size:12px;color:#ccc;">
          <div>Battery: <span id="status-batt">--</span></div>
          <div>Pair code (setup): <code id="status-pair">--</code></div>
          <div>mDNS: <code id="status-mdns">--</code></div>
        </div>
      </div>
    </div>

    <div id="chart-container">
      <small class="muted">Live CO2 history (last """ + str(MAX_WEB_POINTS) + """ samples max)</small>
      <canvas id="chart" width="420" height="140"></canvas>
      <div id="chart-debug"></div>
    </div>

    <form method="POST" action="/">""" + pw_hidden_field + """
      <h2>CO2 &amp; Graph</h2>
      <fieldset>
        <legend>Thresholds</legend>
        <label for="field-low">
          <span class="lbl" data-i18n="lbl_low">Low threshold (ppm)</span>
          <input id="field-low" type="number" name="low" min="400" max="10000"
                 aria-describedby="help-low"
                 value='""" + str(int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))) + """'>
          <span class="help-text" id="help-low" data-i18n="help_low">CO&#x2082; level below this is shown in green.</span>
        </label>
        <label for="field-med">
          <span class="lbl" data-i18n="lbl_med">Medium threshold (ppm)</span>
          <input id="field-med" type="number" name="med" min="400" max="10000"
                 aria-describedby="help-med"
                 value='""" + str(int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))) + """'>
          <span class="help-text" id="help-med" data-i18n="help_med">CO&#x2082; level below this is shown in yellow.</span>
        </label>
        <label for="field-alert">
          <span class="lbl" data-i18n="lbl_alert">Alert threshold (ppm)</span>
          <input id="field-alert" type="number" name="alert" min="400" max="10000"
                 aria-describedby="help-alert"
                 value='""" + str(int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))) + """'>
          <span class="help-text" id="help-alert" data-i18n="help_alert">CO&#x2082; level at or above this triggers an alert.</span>
        </label>
        <label for="field-max-pts">
          <span class="lbl" data-i18n="lbl_max_pts">History buffer (samples)</span>
          <input id="field-max-pts" type="number" name="max_points" min="100" max="50000"
                 value='""" + str(int(max_points)) + """'>
        </label>
        <small>Higher values show longer history but use more memory.</small>
      </fieldset>

      <h2>Password protection</h2>
      <fieldset>
        <legend>Password</legend>
        <label for="field-admin-pw">
          <span class="lbl" data-i18n="lbl_admin_pw">Settings password</span>
          <input id="field-admin-pw" type="password" name="admin_pw" maxlength="64" value=""
                 aria-describedby="help-admin-pw">
          <span class="help-text" id="help-admin-pw" data-i18n="help_admin_pw">Leave blank to disable password protection.</span>
        </label>
        <small class="muted">Leave blank to disable password protection. When a password is set,
          the settings page will prompt you to log in with that password before changes can be made.</small>
      </fieldset>

      <fieldset>
        <legend data-i18n='sec_device'>Device</legend>
        <label><span data-i18n='lbl_lang'>Interface Language / Idioma / Sprache</span><br>
          <select name='lang'>
            <option value='en'""" + (" selected" if current_lang=="en" else "") + """>English</option>
            <option value='es'""" + (" selected" if current_lang=="es" else "") + """>Espa&#241;ol</option>
            <option value='fr'""" + (" selected" if current_lang=="fr" else "") + """>Fran&#231;ais</option>
            <option value='de'""" + (" selected" if current_lang=="de" else "") + """>Deutsch</option>
            <option value='pt'""" + (" selected" if current_lang=="pt" else "") + """>Portugu&#234;s</option>
            <option value='it'""" + (" selected" if current_lang=="it" else "") + """>Italiano</option>
            <option value='ja'""" + (" selected" if current_lang=="ja" else "") + """>\u65e5\u672c\u8a9e</option>
            <option value='zh'""" + (" selected" if current_lang=="zh" else "") + """>\u4e2d\u6587(\u7b80\u4f53)</option>
            <option value='ko'""" + (" selected" if current_lang=="ko" else "") + """>\ud55c\uad6d\uc5b4</option>
          </select>
        </label>
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

      <fieldset>
        <legend>Alerts</legend>
        <label class="check-label">
          <input type="checkbox" name="alerts" value="on" """ + checked_alerts + """
                 data-i18n-aria="aria_alerts_check">
          <span data-i18n="lbl_alerts">Enable color alerts and on-screen alert messages</span>
        </label>
      </fieldset>

      <fieldset class="row">
        <legend data-i18n='sec_display'>Display &amp; Units</legend>
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
        <label class="check-label">
          <input type="checkbox" name="display_flip" id="display-flip"
                 data-i18n-aria="aria_flip_check" """ + ("checked" if settings.get("display_flip") else "") + """>
          <span data-i18n="lbl_flip">Flip display (upside-down mount)</span>
        </label>
        <span class="help-text" data-i18n="help_flip">Rotates the screen 180&#xB0; so the display reads correctly when the device is mounted upside down. Button functions are not affected.</span>
        <label class="check-label">
          <input type="checkbox" name="colorblind_mode" id="colorblind-mode" """ + ("checked" if settings.get("colorblind_mode") else "") + """>
          <span>Colorblind-friendly colors</span>
        </label>
        <span class="help-text">Replaces the red/yellow/green CO&#x2082; indicators with a blue/amber/vermillion palette (Wong colorblind-safe) that is distinguishable for deuteranopia, protanopia, and tritanopia. Takes effect immediately on save.</span>
      </fieldset>

      <h2>Wi-Fi Access Point</h2>
      <fieldset>
        <legend data-i18n='sec_wifi'>Local AP</legend>
        <label>AP SSID
          <input type="text" name="ap_ssid" maxlength="32"
                 value='""" + ap_ssid + """'>
        </label>
        <label>AP password
          <input type="password" name="ap_password" maxlength="63" value="">
        </label>

        <div class="row" style="margin-top:10px;">
          <button type="button" class="btn btn-primary" onclick="location.href='/?regen_ap=1'"
                  data-i18n="lbl_regen">
            Regenerate AP credentials
          </button>
          <div class="muted" style="margin-top:6px;">
            <small>This restarts AP. View the new password on the device (press D2).</small>
          </div>
        </div>
      </fieldset>

      <h2>Wi-Fi Network (client)</h2>
      <fieldset>
        <legend data-i18n="sec_wifi">For LAN + cloud uploads</legend>
        <p class="help-text" data-i18n="help_sta">Enter your home Wi-Fi credentials to connect the device to your network.</p>
        <label for="field-sta-ssid">
          <span class="lbl" data-i18n="lbl_sta_ssid">Network SSID</span>
          <input id="field-sta-ssid" type="text" name="sta_ssid" maxlength="32"
                 data-i18n-placeholder="ph_sta_ssid" placeholder="Your Wi-Fi network name"
                 value='""" + sta_ssid + """'>
        </label>
        <label for="field-sta-pass">
          <span class="lbl" data-i18n="lbl_sta_pass">Network password</span>
          <input id="field-sta-pass" type="password" name="sta_password" maxlength="63" value=""
                 data-i18n-placeholder="ph_sta_pass" placeholder="Your Wi-Fi password">
        </label>
        <small class="muted">
          Tip: after saving STA credentials, <b>hold D2 for ~2 seconds</b> to switch into STA mode.
        </small>
      </fieldset>

      <h2>Cloud telemetry</h2>
      <fieldset>
        <legend data-i18n='sec_cloud'>API data ingest</legend>
        <p class="help-text" data-i18n="help_cloud">Send CO&#x2082; readings to the Know CO&#x2082; cloud dashboard.</p>
        <small class="muted">Onboard your device at <a href=\"https://cloud.knowco2.com\">https://cloud.knowco2.com</a> register and generate a device id and secret to enter.</small>
        <label class="check-label">
          <input type="checkbox" name="cloud_enabled" value="on" """ + cloud_enabled_checked + """
                 data-i18n-aria="aria_cloud_check">
          <span data-i18n="lbl_cloud_en">Enable cloud uploads (requires STA Wi-Fi + token)</span>
        </label>

        <label for="field-cloud-url">
          <span class="lbl" data-i18n="lbl_cloud_url">Cloud API URL</span>
          <input id="field-cloud-url" type="text" name="cloud_api_url" maxlength="200"
                 data-i18n-placeholder="ph_cloud_url" placeholder="https://api.knowco2.com/v1/ingest"
                 value='""" + cloud_api + """'>
        </label>

        <label for="field-cloud-token">
          <span class="lbl" data-i18n="lbl_cloud_token">Device token (secret)</span>
          <input id="field-cloud-token" type="password" name="cloud_device_token" maxlength="128"
                 data-i18n-placeholder="ph_cloud_token" placeholder="Paste your device token here" value="">
          <span class="help-text" data-i18n="lbl_cloud_secret">Device secret / token</span>
        </label>
        <small>Paste token once. It is stored on device and not shown again.</small>

        <label for="field-device-id">
          <span class="lbl" data-i18n="lbl_device_id">Device ID</span>
          <input id="field-device-id" type="text" name="device_id" maxlength="40"
                 data-i18n-placeholder="ph_device_id" placeholder="co2-node-1"
                 aria-describedby="help-device-id"
                 value='""" + device_id + """'>
          <span class="help-text" id="help-device-id" data-i18n="help_device_id">Identifier sent with cloud and MQTT data.</span>
        </label>

        <label for="field-cloud-interval">
          <span class="lbl" data-i18n="lbl_cloud_interval">Upload interval (seconds)</span>
          <input id="field-cloud-interval" type="number" name="cloud_interval_sec" min="15" max="3600"
                 value='""" + str(int(settings.get("cloud_interval_sec", 60))) + """'>
        </label>
        <small class="muted">
          Pairing: create an account, then enter this device's <b>Pair code</b>.
          The cloud app returns a device token you paste here.
        </small>
      </fieldset>

      <!-- Device identity section removed; Device ID is now under Cloud telemetry and local endpoints moved to bottom. -->

      <fieldset>
        <legend data-i18n='sec_mqtt'>MQTT Broker (Home Assistant etc.)</legend>
        <p class="help-text" data-i18n="help_mqtt">Publish readings to a local MQTT broker (e.g. Home Assistant).</p>
        <label class="check-label">
          <input type='checkbox' name='mqtt_enabled' """ + mqtt_checked + """
                 data-i18n-aria="aria_mqtt_check">
          <span data-i18n="lbl_mqtt_en">Enable MQTT publishing</span>
        </label>
        <label for="field-mqtt-broker">
          <span class="lbl" data-i18n="lbl_mqtt_broker">Broker hostname/IP</span>
          <input id="field-mqtt-broker" type='text' name='mqtt_broker' value='""" + mqtt_broker + """'
                 data-i18n-placeholder="ph_mqtt_broker" placeholder='192.168.1.x or mqtt.example.com'>
        </label>
        <label for="field-mqtt-port">
          <span class="lbl" data-i18n="lbl_mqtt_port">Port</span>
          <input id="field-mqtt-port" type='number' name='mqtt_port' value='""" + mqtt_port + """' min='1' max='65535'>
        </label>
        <label for="field-mqtt-user">
          <span class="lbl" data-i18n="lbl_mqtt_user">Username (optional)</span>
          <input id="field-mqtt-user" type='text' name='mqtt_user' value='""" + mqtt_user + """'>
        </label>
        <label for="field-mqtt-pass">
          <span class="lbl" data-i18n="lbl_mqtt_pass">Password (optional)</span>
          <input id="field-mqtt-pass" type='password' name='mqtt_pass' placeholder='leave blank to keep current'>
        </label>
        <label for="field-mqtt-prefix">
          <span class="lbl" data-i18n="lbl_mqtt_prefix">Topic prefix</span>
          <input id="field-mqtt-prefix" type='text' name='mqtt_topic_prefix' value='""" + mqtt_topic_prefix + """'
                 data-i18n-placeholder="ph_mqtt_prefix" placeholder='knowco2'>
        </label>
        <label for="field-mqtt-interval">
          <span class="lbl" data-i18n="lbl_mqtt_interval">Publish interval (seconds)</span>
          <input id="field-mqtt-interval" type='number' name='mqtt_interval_sec' value='""" + mqtt_interval + """' min='15' max='3600'>
        </label>
        <small>Topics: &lt;prefix&gt;/co2, &lt;prefix&gt;/temp_c, &lt;prefix&gt;/rh</small>
      </fieldset>
      <fieldset>
        <legend data-i18n='sec_aio'>Adafruit IO</legend>
        <p class="help-text" data-i18n="help_aio">Publish readings to Adafruit IO feeds.</p>
        <label class="check-label">
          <input type='checkbox' name='aio_enabled' """ + aio_checked + """
                 data-i18n-aria="aria_aio_check">
          <span data-i18n="lbl_aio_en">Enable Adafruit IO</span>
        </label>
        <label for="field-aio-user">
          <span class="lbl" data-i18n="lbl_aio_user">AIO Username</span>
          <input id="field-aio-user" type='text' name='aio_username' value='""" + aio_username + """'>
        </label>
        <label for="field-aio-key">
          <span class="lbl">AIO Key</span>
          <input id="field-aio-key" type='password' name='aio_key' placeholder='leave blank to keep current'>
        </label>
        <label for="field-aio-group">
          <span class="lbl" data-i18n="lbl_aio_group">Feed group key</span>
          <input id="field-aio-group" type='text' name='aio_group_key' value='""" + aio_group + """'
                 data-i18n-placeholder="ph_aio_group" placeholder='knowco2'>
        </label>
        <label for="field-aio-interval">
          <span class="lbl" data-i18n="lbl_aio_interval">Publish interval (seconds)</span>
          <input id="field-aio-interval" type='number' name='aio_interval_sec' value='""" + aio_interval + """' min='15' max='3600'>
        </label>
        <small>Feeds: &lt;group&gt;.co2, &lt;group&gt;.temperature, &lt;group&gt;.humidity</small>
      </fieldset>
      <fieldset>
        <legend data-i18n='sec_dim'>Display Dimming Schedule</legend>
        <p class="help-text" data-i18n="help_dim">Automatically reduce display brightness during set hours. Requires NTP time sync.</p>
        <label class="check-label">
          <input type='checkbox' name='dim_enabled' """ + dim_checked + """
                 data-i18n-aria="aria_dim_check">
          <span data-i18n="lbl_dim_en">Enable scheduled dimming (requires NTP)</span>
        </label>
        <label for="field-dim-start">
          <span class="lbl" data-i18n="lbl_dim_start">Dim start hour (0-23)</span>
          <input id="field-dim-start" type='number' name='dim_start_hour' value='""" + dim_start + """' min='0' max='23'>
        </label>
        <label for="field-dim-end">
          <span class="lbl" data-i18n="lbl_dim_end">Dim end hour (0-23)</span>
          <input id="field-dim-end" type='number' name='dim_end_hour' value='""" + dim_end + """' min='0' max='23'>
        </label>
        <label for="field-dim-bright">
          <span class="lbl" data-i18n="lbl_dim_bright">Brightness during dim period (0-100%)</span>
          <input id="field-dim-bright" type='number' name='dim_brightness' value='""" + dim_brightness + """' min='0' max='100'>
        </label>
        <small>Example: start=22, end=7 dims from 10 PM to 7 AM.</small>
      </fieldset>

      <div class="row">
        <button type="submit" class="btn btn-primary btn-block" data-i18n="save">Save settings</button>
      </div>
      <div class="row">
        <small>
          <b>Local endpoints:</b><br>
          • <code>GET /status</code> → live JSON status<br>
          • <code>GET /data</code> → CO₂ history JSON (up to """ + str(MAX_WEB_POINTS) + """ points)<br>
          • <code>GET /export.csv</code> → download CO₂ history as CSV
        </small>
      </div>
      <div class="row muted">
        <small>Settings are saved to <code>settings.json</code>. If you see "USB mode: settings won't save", eject CIRCUITPY from your computer.</small>
      </div>
      <nav class="page-nav" aria-label="Page navigation">
        <a href="/calibration" data-i18n="nav_calib">Calibration</a>
        <a href="/update" data-i18n="nav_ota" style="color:var(--danger)">Firmware Update</a>
      </nav>
    </form>
  </div>
  <script>
    let lastStatus = null;
    const SAMPLE_PERIOD_SEC = 5;
    const initialPoints = """ + initial_json + """;
    const CB_MODE = """ + web_cb_mode + """;

    // Color palettes — matches the device firmware schemes exactly.
    const PAL_NORMAL = { low: '#00e676', med: '#fff176', alert: '#ff5252',
                         zoneLow: '#00e676', zoneMed: '#fff176', zoneHigh: '#ff5252' };
    const PAL_CB     = { low: '#56B4E9', med: '#E69F00', alert: '#D55E00',
                         zoneLow: '#56B4E9', zoneMed: '#E69F00', zoneHigh: '#D55E00' };
    const PAL = CB_MODE ? PAL_CB : PAL_NORMAL;

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
    const statusRateEl  = document.getElementById('status-rate');

    function drawChart(points) {
      const w = canvas.width;
      const h = canvas.height;

      const padL = 42;   // wider left pad for Y-axis tick labels
      const padR = 8;
      const padT = 14;
      const padB = 24;   // taller bottom pad for X-axis time labels
      const cw = w - padL - padR;
      const ch = h - padT - padB;

      ctx.fillStyle = '#050505';
      ctx.fillRect(0, 0, w, h);

      if (!points || points.length === 0) {
        ctx.fillStyle = '#aaaaaa';
        ctx.font = '13px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('No data yet', w/2, h/2);
        ctx.textAlign = 'left';
        debugEl.textContent = 'Samples: 0';
        return;
      }

      const lowT   = (lastStatus && typeof lastStatus.low_threshold   === 'number') ? lastStatus.low_threshold   : 800;
      const medT   = (lastStatus && typeof lastStatus.med_threshold   === 'number') ? lastStatus.med_threshold   : 1200;
      const alertT = (lastStatus && typeof lastStatus.alert_threshold === 'number') ? lastStatus.alert_threshold : 1500;

      let min = Math.min.apply(null, points);
      let max = Math.max.apply(null, points);
      min = Math.min(min, 400, lowT);
      max = Math.max(max, 800, alertT + 100);
      if (min === max) { min -= 50; max += 50; }

      function yFor(v) {
        const t = (v - min) / (max - min);
        return padT + (1 - t) * ch;
      }
      function xFor(i) {
        const denom = Math.max(points.length - 1, 1);
        return padL + (i / denom) * cw;
      }
      function segColor(v) {
        if (v < lowT)   return PAL.low;
        if (v < medT)   return PAL.med;
        if (v < alertT) return PAL.alert;
        return PAL.alert;
      }

      // ── Zone background fills ─────────────────────────────────────────
      ctx.globalAlpha = 0.08;
      ctx.fillStyle = PAL.zoneLow;
      ctx.fillRect(padL, yFor(lowT),   cw, yFor(min)   - yFor(lowT));
      ctx.fillStyle = PAL.zoneMed;
      ctx.fillRect(padL, yFor(medT),   cw, yFor(lowT)  - yFor(medT));
      ctx.fillStyle = PAL.zoneHigh;
      ctx.fillRect(padL, yFor(padT),   cw, yFor(medT)  - yFor(padT));
      ctx.globalAlpha = 1.0;

      // ── Y-axis ticks and horizontal grid lines ────────────────────────
      const yTicks = [];
      const candidates = [min, lowT, medT, alertT, max];
      for (let i = 0; i < candidates.length; i++) {
        const v = candidates[i];
        if (yTicks.every(u => Math.abs(u - v) > 20)) yTicks.push(v);
      }

      ctx.font = '10px monospace';
      ctx.textAlign = 'right';
      for (let i = 0; i < yTicks.length; i++) {
        const v  = yTicks[i];
        const yy = yFor(v);
        // Horizontal grid line
        ctx.strokeStyle = '#1e1e1e';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, yy);
        ctx.lineTo(padL + cw, yy);
        ctx.stroke();
        // Threshold dashes coloured by zone
        if (v === lowT || v === medT || v === alertT) {
          ctx.strokeStyle = (v === lowT ? PAL.low : v === medT ? PAL.med : PAL.alert);
          ctx.setLineDash([4, 4]);
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(padL + cw, yy); ctx.stroke();
          ctx.setLineDash([]);
        }
        // Y-axis label
        const color = (v === lowT ? PAL.low : v === medT ? PAL.med : v === alertT ? PAL.alert : '#666666');
        ctx.fillStyle = color;
        ctx.fillText(v.toFixed(0), padL - 5, yy + 3);
      }
      // "ppm" Y-axis unit label rotated vertically
      ctx.save();
      ctx.translate(10, padT + ch / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.font = '9px sans-serif';
      ctx.fillStyle = '#555555';
      ctx.textAlign = 'center';
      ctx.fillText('ppm', 0, 0);
      ctx.restore();
      ctx.textAlign = 'left';

      // ── Axis border lines ─────────────────────────────────────────────
      ctx.strokeStyle = '#555555';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(padL, padT);
      ctx.lineTo(padL, padT + ch);          // Y-axis
      ctx.lineTo(padL + cw, padT + ch);     // X-axis
      ctx.stroke();

      // ── Data line ────────────────────────────────────────────────────
      ctx.lineWidth = 2;
      for (let i = 1; i < points.length; i++) {
        const v1 = points[i];
        ctx.strokeStyle = segColor(v1);
        ctx.beginPath();
        ctx.moveTo(xFor(i-1), yFor(points[i-1]));
        ctx.lineTo(xFor(i),   yFor(v1));
        ctx.stroke();
      }

      // ── X-axis time labels ────────────────────────────────────────────
      const spanSec = (points.length - 1) * SAMPLE_PERIOD_SEC;
      function fmtTime(sec) {
        if (sec < 90) return '-' + sec.toFixed(0) + 's';
        return '-' + (sec / 60).toFixed(1) + 'm';
      }
      ctx.font = '10px monospace';
      ctx.fillStyle = '#888888';
      ctx.textAlign = 'left';
      ctx.fillText(fmtTime(spanSec), padL + 2, padT + ch + 14);   // left = oldest
      ctx.textAlign = 'center';
      ctx.fillText(fmtTime(spanSec / 2), padL + cw / 2, padT + ch + 14);  // mid
      ctx.textAlign = 'right';
      ctx.fillText('now', padL + cw - 2, padT + ch + 14);          // right = newest
      ctx.textAlign = 'left';

      // Latest-point dot
      if (points.length > 0) {
        const lx = xFor(points.length - 1);
        const ly = yFor(points[points.length - 1]);
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(lx, ly, 3, 0, 2 * Math.PI);
        ctx.fill();
      }

      debugEl.textContent = 'Samples: ' + points.length + ' · span: ' + (spanSec/60).toFixed(1) + 'm';
    }

    function updateStatusCard(s) {
      lastStatus = s;
      if (!s) return;
      statusCo2El.textContent = (typeof s.co2 === 'number') ? s.co2.toFixed(0) : '--';
      statusArrowEl.textContent = s.trend_arrow || '-';
      // Show rate of change if provided: format with sign and one decimal place (ppm/s)
      if (typeof s.rate_of_change === 'number') {
        const rc = s.rate_of_change;
        statusRateEl.textContent = (rc >= 0 ? '+' : '') + rc.toFixed(1) + ' ppm/s';
      } else {
        statusRateEl.textContent = '';
      }
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
  <p style='color:#666;font-size:12px;margin-top:12px' data-i18n='note_https'>Note: Local web server uses HTTP (HTTPS not supported in CircuitPython). Traffic stays on your LAN.</p>
<script>
// ---- Know CO2 Web UI i18n ----
// Translations for key UI strings.  Applied client-side so no device reboot needed.
// Language preference is stored in localStorage and also sent to device on form save.
var T = {
  en: {
    title:"Know CO\u2082 Settings",save:"Save Settings",
    sec_display:"Display & Thresholds",sec_wifi:"Wi-Fi",
    sec_cloud:"Cloud Upload",sec_mqtt:"MQTT Broker",
    sec_aio:"Adafruit IO",sec_dim:"Display Dimming",
    sec_device:"Device",sec_calib:"Calibration",
    lbl_low:"Low threshold (ppm)",lbl_med:"Medium threshold (ppm)",
    lbl_alert:"Alert threshold (ppm)",lbl_temp:"Temperature unit",
    lbl_alerts:"Enable alerts",lbl_scale:"Graph scale",
    lbl_ap_ssid:"AP network name",lbl_ap_pass:"AP password",
    lbl_sta_ssid:"Home Wi-Fi SSID",lbl_sta_pass:"Home Wi-Fi password",
    lbl_cloud_url:"API URL",lbl_cloud_token:"Device token",
    lbl_cloud_interval:"Upload interval (seconds)",lbl_cloud_en:"Enable cloud upload",
    lbl_mqtt_en:"Enable MQTT publishing",lbl_mqtt_broker:"Broker hostname/IP",
    lbl_aio_en:"Enable Adafruit IO",lbl_aio_user:"AIO Username",
    lbl_dim_en:"Enable scheduled dimming (requires NTP)",
    lbl_dim_start:"Dim start hour (0-23)",lbl_dim_end:"Dim end hour (0-23)",
    lbl_dim_bright:"Brightness during dim period (0-100%)",
    lbl_lang:"Interface Language",lbl_admin_pw:"Settings password",
    lbl_device_id:"Device ID",btn_ota:"OTA Firmware Update",
    note_dim:"Example: start=22, end=7 dims from 10 PM to 7 AM.",
    note_https:"Note: Local web server uses HTTP (HTTPS not supported in CircuitPython). Traffic stays on your LAN.",
    help_low:"CO\u2082 level below this is shown in green.",
    help_med:"CO\u2082 level below this is shown in yellow.",
    help_alert:"CO\u2082 level at or above this triggers an alert.",
    help_device_id:"Identifier sent with cloud and MQTT data.",
    help_admin_pw:"Leave blank to disable password protection.",
    help_sta:"Enter your home Wi-Fi credentials to connect the device to your network.",
    help_cloud:"Send CO\u2082 readings to the Know CO\u2082 cloud dashboard.",
    help_mqtt:"Publish readings to a local MQTT broker (e.g. Home Assistant).",
    help_aio:"Publish readings to Adafruit IO feeds.",
    help_dim:"Automatically reduce display brightness during set hours. Requires NTP time sync.",
    help_ota:"Download and install new firmware. The device will reboot after a successful update.",
    lbl_regen:"Regenerate AP credentials",
    lbl_sta_connect:"Connect to this network on next reboot",
    lbl_mode_text:"Text",lbl_mode_big:"Big CO\u2082",lbl_mode_graph:"Graph",
    lbl_scale_fixed:"400\u20132000 ppm (tight)",lbl_scale_wide:"400\u20133000 ppm (wide)",lbl_scale_auto:"Automatic",
    lbl_max_pts:"History buffer (samples)",
    lbl_cloud_secret:"Device secret / token",
    lbl_mqtt_port:"Port",lbl_mqtt_user:"Username (optional)",lbl_mqtt_pass:"Password (optional)",
    lbl_mqtt_prefix:"Topic prefix",lbl_mqtt_interval:"Publish interval (seconds)",
    lbl_aio_group:"Feed group key",lbl_aio_interval:"Publish interval (seconds)",
    nav_calib:"Calibration",nav_back:"Back to Settings",nav_ota:"Firmware Update",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Your Wi-Fi network name",ph_sta_pass:"Your Wi-Fi password",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Paste your device token here",
    ph_mqtt_broker:"192.168.1.x or mqtt.example.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"your-adafruit-username",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Toggle CO\u2082 alert notifications",
    aria_cloud_check:"Toggle cloud data upload",
    aria_mqtt_check:"Toggle MQTT publishing",
    aria_aio_check:"Toggle Adafruit IO publishing",
    aria_dim_check:"Toggle scheduled display dimming",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Skip to main content"
  },
  es: {
    title:"Ajustes Know CO\u2082",save:"Guardar ajustes",
    sec_display:"Pantalla y umbrales",sec_wifi:"Wi-Fi",
    sec_cloud:"Subida a la nube",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Atenuacion de pantalla",
    sec_device:"Dispositivo",sec_calib:"Calibracion",
    lbl_low:"Umbral bajo (ppm)",lbl_med:"Umbral medio (ppm)",
    lbl_alert:"Umbral de alerta (ppm)",lbl_temp:"Unidad de temperatura",
    lbl_alerts:"Activar alertas",lbl_scale:"Escala del grafico",
    lbl_ap_ssid:"Nombre de red AP",lbl_ap_pass:"Contrasena AP",
    lbl_sta_ssid:"SSID Wi-Fi del hogar",lbl_sta_pass:"Contrasena Wi-Fi",
    lbl_cloud_url:"URL de la API",lbl_cloud_token:"Token del dispositivo",
    lbl_cloud_interval:"Intervalo de subida (segundos)",lbl_cloud_en:"Activar subida a la nube",
    lbl_mqtt_en:"Activar publicacion MQTT",lbl_mqtt_broker:"Servidor/IP del broker",
    lbl_aio_en:"Activar Adafruit IO",lbl_aio_user:"Usuario AIO",
    lbl_dim_en:"Activar atenuacion programada (requiere NTP)",
    lbl_dim_start:"Hora inicio (0-23)",lbl_dim_end:"Hora fin (0-23)",
    lbl_dim_bright:"Brillo durante atenuacion (0-100%)",
    lbl_lang:"Idioma de la interfaz",lbl_admin_pw:"Contrasena de ajustes",
    lbl_device_id:"ID del dispositivo",btn_ota:"Actualizar firmware (OTA)",
    note_dim:"Ejemplo: inicio=22, fin=7 atenua de 22:00 a 07:00.",
    note_https:"Nota: El servidor web usa HTTP. El trafico permanece en su red local.",
    help_low:"El nivel de CO\u2082 por debajo de este se muestra en verde.",
    help_med:"El nivel de CO\u2082 por debajo de este se muestra en amarillo.",
    help_alert:"El nivel de CO\u2082 igual o superior a este activa una alerta.",
    help_device_id:"Identificador enviado con los datos de nube y MQTT.",
    help_admin_pw:"Dejar en blanco para desactivar la proteccion por contrasena.",
    help_sta:"Introduce las credenciales de tu Wi-Fi para conectar el dispositivo.",
    help_cloud:"Envia lecturas de CO\u2082 al panel de Know CO\u2082.",
    help_mqtt:"Publica lecturas en un broker MQTT local (p. ej. Home Assistant).",
    help_aio:"Publica lecturas en feeds de Adafruit IO.",
    help_dim:"Reduce automaticamente el brillo en las horas configuradas. Requiere NTP.",
    help_ota:"Descarga e instala nuevo firmware. El dispositivo se reiniciara.",
    lbl_regen:"Regenerar credenciales AP",lbl_sta_connect:"Conectar en el proximo reinicio",
    lbl_mode_text:"Texto",lbl_mode_big:"CO\u2082 grande",lbl_mode_graph:"Grafico",
    lbl_scale_fixed:"400\u20132000 ppm (ajustado)",lbl_scale_wide:"400\u20133000 ppm (amplio)",lbl_scale_auto:"Automatico",
    lbl_max_pts:"Buffer de historial (muestras)",lbl_cloud_secret:"Secreto/token del dispositivo",
    lbl_mqtt_port:"Puerto",lbl_mqtt_user:"Usuario (opcional)",lbl_mqtt_pass:"Contrasena (opcional)",
    lbl_mqtt_prefix:"Prefijo del topic",lbl_mqtt_interval:"Intervalo de publicacion (segundos)",
    lbl_aio_group:"Clave de grupo de feeds",lbl_aio_interval:"Intervalo de publicacion (segundos)",
    nav_calib:"Calibracion",nav_back:"Volver a Ajustes",nav_ota:"Actualizar firmware",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Nombre de tu red Wi-Fi",ph_sta_pass:"Contrasena Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Pega tu token aqui",
    ph_mqtt_broker:"192.168.1.x o mqtt.ejemplo.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"tu-usuario-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Activar o desactivar alertas de CO\u2082",
    aria_cloud_check:"Activar o desactivar subida de datos a la nube",
    aria_mqtt_check:"Activar o desactivar publicacion MQTT",
    aria_aio_check:"Activar o desactivar publicacion en Adafruit IO",
    aria_dim_check:"Activar o desactivar atenuacion programada",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Saltar al contenido principal"
  },
  fr: {
    title:"Parametres Know CO\u2082",save:"Enregistrer",
    sec_display:"Affichage et seuils",sec_wifi:"Wi-Fi",
    sec_cloud:"Envoi vers le cloud",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Attenuation ecran",
    sec_device:"Appareil",sec_calib:"Calibration",
    lbl_low:"Seuil bas (ppm)",lbl_med:"Seuil moyen (ppm)",
    lbl_alert:"Seuil d'alerte (ppm)",lbl_temp:"Unite de temperature",
    lbl_alerts:"Activer les alertes",lbl_scale:"Echelle du graphique",
    lbl_ap_ssid:"Nom du reseau AP",lbl_ap_pass:"Mot de passe AP",
    lbl_sta_ssid:"SSID Wi-Fi domicile",lbl_sta_pass:"Mot de passe Wi-Fi",
    lbl_cloud_url:"URL de l'API",lbl_cloud_token:"Token de l'appareil",
    lbl_cloud_interval:"Intervalle d'envoi (secondes)",lbl_cloud_en:"Activer l'envoi cloud",
    lbl_mqtt_en:"Activer la publication MQTT",lbl_mqtt_broker:"Hote/IP du broker",
    lbl_aio_en:"Activer Adafruit IO",lbl_aio_user:"Utilisateur AIO",
    lbl_dim_en:"Activer l'attenuation programmee (NTP requis)",
    lbl_dim_start:"Heure debut (0-23)",lbl_dim_end:"Heure fin (0-23)",
    lbl_dim_bright:"Luminosite en periode d'attenuation (0-100%)",
    lbl_lang:"Langue de l'interface",lbl_admin_pw:"Mot de passe des parametres",
    lbl_device_id:"ID de l'appareil",btn_ota:"Mise a jour OTA",
    note_dim:"Exemple: debut=22, fin=7 attenue de 22h a 7h.",
    note_https:"Note: Le serveur web utilise HTTP. Le trafic reste sur votre reseau local.",
    help_low:"Le niveau de CO\u2082 en dessous est affiche en vert.",
    help_med:"Le niveau de CO\u2082 en dessous est affiche en jaune.",
    help_alert:"Le niveau de CO\u2082 egal ou superieur declenche une alerte.",
    help_device_id:"Identifiant envoye avec les donnees cloud et MQTT.",
    help_admin_pw:"Laisser vide pour desactiver la protection par mot de passe.",
    help_sta:"Entrez vos identifiants Wi-Fi pour connecter l'appareil.",
    help_cloud:"Envoyez les mesures de CO\u2082 au tableau de bord Know CO\u2082.",
    help_mqtt:"Publiez les mesures sur un broker MQTT local (ex. Home Assistant).",
    help_aio:"Publiez les mesures sur des feeds Adafruit IO.",
    help_dim:"Reduit automatiquement la luminosite aux heures configurees. NTP requis.",
    help_ota:"Telechargez et installez un nouveau firmware. L'appareil redemarrera.",
    lbl_regen:"Regenerer les identifiants AP",lbl_sta_connect:"Connecter au prochain redemarrage",
    lbl_mode_text:"Texte",lbl_mode_big:"Grand CO\u2082",lbl_mode_graph:"Graphique",
    lbl_scale_fixed:"400\u20132000 ppm (serre)",lbl_scale_wide:"400\u20133000 ppm (large)",lbl_scale_auto:"Automatique",
    lbl_max_pts:"Tampon d'historique (echantillons)",lbl_cloud_secret:"Secret/token de l'appareil",
    lbl_mqtt_port:"Port",lbl_mqtt_user:"Utilisateur (optionnel)",lbl_mqtt_pass:"Mot de passe (optionnel)",
    lbl_mqtt_prefix:"Prefixe du sujet",lbl_mqtt_interval:"Intervalle de publication (secondes)",
    lbl_aio_group:"Cle du groupe de feeds",lbl_aio_interval:"Intervalle de publication (secondes)",
    nav_calib:"Calibration",nav_back:"Retour aux parametres",nav_ota:"Mise a jour firmware",
    ph_device_id:"co2-noeud-1",ph_sta_ssid:"Nom de votre reseau Wi-Fi",ph_sta_pass:"Mot de passe Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Collez votre token ici",
    ph_mqtt_broker:"192.168.1.x ou mqtt.exemple.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"votre-utilisateur-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Activer ou desactiver les alertes CO\u2082",
    aria_cloud_check:"Activer ou desactiver l'envoi de donnees vers le cloud",
    aria_mqtt_check:"Activer ou desactiver la publication MQTT",
    aria_aio_check:"Activer ou desactiver la publication Adafruit IO",
    aria_dim_check:"Activer ou desactiver l'attenuation programmee",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Aller au contenu principal"
  },
  de: {
    title:"Know CO\u2082 Einstellungen",save:"Einstellungen speichern",
    sec_display:"Anzeige & Schwellenwerte",sec_wifi:"Wi-Fi",
    sec_cloud:"Cloud-Upload",sec_mqtt:"MQTT-Broker",
    sec_aio:"Adafruit IO",sec_dim:"Bildschirmabdunkelung",
    sec_device:"Gerat",sec_calib:"Kalibrierung",
    lbl_low:"Niedriger Schwellenwert (ppm)",lbl_med:"Mittlerer Schwellenwert (ppm)",
    lbl_alert:"Alarmschwelle (ppm)",lbl_temp:"Temperatureinheit",
    lbl_alerts:"Alarme aktivieren",lbl_scale:"Diagrammskalierung",
    lbl_ap_ssid:"AP-Netzwerkname",lbl_ap_pass:"AP-Passwort",
    lbl_sta_ssid:"WLAN-SSID zuhause",lbl_sta_pass:"WLAN-Passwort",
    lbl_cloud_url:"API-URL",lbl_cloud_token:"Gerate-Token",
    lbl_cloud_interval:"Upload-Intervall (Sekunden)",lbl_cloud_en:"Cloud-Upload aktivieren",
    lbl_mqtt_en:"MQTT-Veroffentlichung aktivieren",lbl_mqtt_broker:"Broker-Host/IP",
    lbl_aio_en:"Adafruit IO aktivieren",lbl_aio_user:"AIO-Benutzername",
    lbl_dim_en:"Geplante Abdunkelung aktivieren (NTP erforderlich)",
    lbl_dim_start:"Startzeit (0-23)",lbl_dim_end:"Endzeit (0-23)",
    lbl_dim_bright:"Helligkeit wahrend Abdunkelung (0-100%)",
    lbl_lang:"Oberflachensprache",lbl_admin_pw:"Einstellungspasswort",
    lbl_device_id:"Gerate-ID",btn_ota:"Firmware-Update (OTA)",
    note_dim:"Beispiel: Start=22, Ende=7 dunkelt von 22 bis 7 Uhr ab.",
    note_https:"Hinweis: Webserver nutzt HTTP. Datenverkehr bleibt im lokalen Netz.",
    help_low:"CO\u2082-Wert darunter wird grun angezeigt.",
    help_med:"CO\u2082-Wert darunter wird gelb angezeigt.",
    help_alert:"CO\u2082-Wert gleich oder daruber lost Alarm aus.",
    help_device_id:"Kennung, die mit Cloud- und MQTT-Daten gesendet wird.",
    help_admin_pw:"Leer lassen, um Passwortschutz zu deaktivieren.",
    help_sta:"WLAN-Zugangsdaten eingeben, um das Gerat zu verbinden.",
    help_cloud:"CO\u2082-Messwerte an das Know CO\u2082-Dashboard senden.",
    help_mqtt:"Messwerte an einen lokalen MQTT-Broker senden (z.B. Home Assistant).",
    help_aio:"Messwerte an Adafruit IO-Feeds senden.",
    help_dim:"Displayhelligkeit automatisch reduzieren. NTP erforderlich.",
    help_ota:"Neue Firmware herunterladen und installieren. Gerat startet neu.",
    lbl_regen:"AP-Zugangsdaten neu generieren",lbl_sta_connect:"Beim nachsten Neustart verbinden",
    lbl_mode_text:"Text",lbl_mode_big:"Grosses CO\u2082",lbl_mode_graph:"Diagramm",
    lbl_scale_fixed:"400\u20132000 ppm (eng)",lbl_scale_wide:"400\u20133000 ppm (weit)",lbl_scale_auto:"Automatisch",
    lbl_max_pts:"Verlaufspuffer (Messwerte)",lbl_cloud_secret:"Gerate-Geheimnis/Token",
    lbl_mqtt_port:"Port",lbl_mqtt_user:"Benutzer (optional)",lbl_mqtt_pass:"Passwort (optional)",
    lbl_mqtt_prefix:"Topic-Prafix",lbl_mqtt_interval:"Veroffentlichungsintervall (Sekunden)",
    lbl_aio_group:"Feed-Gruppenschlussel",lbl_aio_interval:"Veroffentlichungsintervall (Sekunden)",
    nav_calib:"Kalibrierung",nav_back:"Zuruck zu Einstellungen",nav_ota:"Firmware-Update",
    ph_device_id:"co2-knoten-1",ph_sta_ssid:"Ihr WLAN-Netzwerkname",ph_sta_pass:"Ihr WLAN-Passwort",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Token hier einfugen",
    ph_mqtt_broker:"192.168.1.x oder mqtt.beispiel.de",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"ihr-adafruit-benutzername",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"CO\u2082-Benachrichtigungen ein- oder ausschalten",
    aria_cloud_check:"Cloud-Datenuploads ein- oder ausschalten",
    aria_mqtt_check:"MQTT-Veroffentlichung ein- oder ausschalten",
    aria_aio_check:"Adafruit IO Veroffentlichung ein- oder ausschalten",
    aria_dim_check:"Geplante Abdunkelung ein- oder ausschalten",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Zum Hauptinhalt springen"
  },
  pt: {
    title:"Configuracoes Know CO\u2082",save:"Salvar configuracoes",
    sec_display:"Tela e limiares",sec_wifi:"Wi-Fi",
    sec_cloud:"Envio para nuvem",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Reducao do brilho",
    sec_device:"Dispositivo",sec_calib:"Calibracao",
    lbl_low:"Limiar baixo (ppm)",lbl_med:"Limiar medio (ppm)",
    lbl_alert:"Limiar de alerta (ppm)",lbl_temp:"Unidade de temperatura",
    lbl_alerts:"Ativar alertas",lbl_scale:"Escala do grafico",
    lbl_ap_ssid:"Nome da rede AP",lbl_ap_pass:"Senha AP",
    lbl_sta_ssid:"SSID Wi-Fi casa",lbl_sta_pass:"Senha Wi-Fi",
    lbl_cloud_url:"URL da API",lbl_cloud_token:"Token do dispositivo",
    lbl_cloud_interval:"Intervalo de envio (segundos)",lbl_cloud_en:"Ativar envio para nuvem",
    lbl_mqtt_en:"Ativar publicacao MQTT",lbl_mqtt_broker:"Host/IP do broker",
    lbl_aio_en:"Ativar Adafruit IO",lbl_aio_user:"Usuario AIO",
    lbl_dim_en:"Ativar reducao de brilho programada (requer NTP)",
    lbl_dim_start:"Hora de inicio (0-23)",lbl_dim_end:"Hora de fim (0-23)",
    lbl_dim_bright:"Brilho durante reducao (0-100%)",
    lbl_lang:"Idioma da interface",lbl_admin_pw:"Senha das configuracoes",
    lbl_device_id:"ID do dispositivo",btn_ota:"Atualizacao OTA",
    note_dim:"Exemplo: inicio=22, fim=7 reduz das 22h as 7h.",
    note_https:"Nota: Servidor web usa HTTP. Trafego fica na rede local.",
    help_low:"Nivel de CO\u2082 abaixo deste e exibido em verde.",
    help_med:"Nivel de CO\u2082 abaixo deste e exibido em amarelo.",
    help_alert:"Nivel de CO\u2082 igual ou acima deste dispara um alerta.",
    help_device_id:"Identificador enviado com dados de nuvem e MQTT.",
    help_admin_pw:"Deixe em branco para desativar a protecao por senha.",
    help_sta:"Insira as credenciais Wi-Fi para conectar o dispositivo.",
    help_cloud:"Envie leituras de CO\u2082 para o painel Know CO\u2082.",
    help_mqtt:"Publique leituras em um broker MQTT local (ex. Home Assistant).",
    help_aio:"Publique leituras em feeds do Adafruit IO.",
    help_dim:"Reduce automaticamente o brilho nos horarios configurados. Requer NTP.",
    help_ota:"Baixe e instale novo firmware. O dispositivo vai reiniciar.",
    lbl_regen:"Regenerar credenciais AP",lbl_sta_connect:"Conectar no proximo reinicio",
    lbl_mode_text:"Texto",lbl_mode_big:"CO\u2082 grande",lbl_mode_graph:"Grafico",
    lbl_scale_fixed:"400\u20132000 ppm (estreito)",lbl_scale_wide:"400\u20133000 ppm (largo)",lbl_scale_auto:"Automatico",
    lbl_max_pts:"Buffer de historico (amostras)",lbl_cloud_secret:"Segredo/token do dispositivo",
    lbl_mqtt_port:"Porta",lbl_mqtt_user:"Usuario (opcional)",lbl_mqtt_pass:"Senha (opcional)",
    lbl_mqtt_prefix:"Prefixo do topico",lbl_mqtt_interval:"Intervalo de publicacao (segundos)",
    lbl_aio_group:"Chave do grupo de feeds",lbl_aio_interval:"Intervalo de publicacao (segundos)",
    nav_calib:"Calibracao",nav_back:"Voltar as Configuracoes",nav_ota:"Atualizar firmware",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Nome da sua rede Wi-Fi",ph_sta_pass:"Senha Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Cole seu token aqui",
    ph_mqtt_broker:"192.168.1.x ou mqtt.exemplo.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"seu-usuario-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Ativar ou desativar alertas de CO\u2082",
    aria_cloud_check:"Ativar ou desativar upload de dados para a nuvem",
    aria_mqtt_check:"Ativar ou desativar publicacao MQTT",
    aria_aio_check:"Ativar ou desativar publicacao no Adafruit IO",
    aria_dim_check:"Ativar ou desativar reducao de brilho programada",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Ir para o conteudo principal"
  },
  it: {
    title:"Impostazioni Know CO\u2082",save:"Salva impostazioni",
    sec_display:"Display e soglie",sec_wifi:"Wi-Fi",
    sec_cloud:"Caricamento cloud",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Attenuazione display",
    sec_device:"Dispositivo",sec_calib:"Calibrazione",
    lbl_low:"Soglia bassa (ppm)",lbl_med:"Soglia media (ppm)",
    lbl_alert:"Soglia di allarme (ppm)",lbl_temp:"Unita di temperatura",
    lbl_alerts:"Abilita allarmi",lbl_scale:"Scala del grafico",
    lbl_ap_ssid:"Nome rete AP",lbl_ap_pass:"Password AP",
    lbl_sta_ssid:"SSID Wi-Fi casa",lbl_sta_pass:"Password Wi-Fi",
    lbl_cloud_url:"URL API",lbl_cloud_token:"Token dispositivo",
    lbl_cloud_interval:"Intervallo upload (secondi)",lbl_cloud_en:"Abilita upload cloud",
    lbl_mqtt_en:"Abilita pubblicazione MQTT",lbl_mqtt_broker:"Host/IP broker",
    lbl_aio_en:"Abilita Adafruit IO",lbl_aio_user:"Utente AIO",
    lbl_dim_en:"Abilita attenuazione programmata (richiede NTP)",
    lbl_dim_start:"Ora inizio (0-23)",lbl_dim_end:"Ora fine (0-23)",
    lbl_dim_bright:"Luminosita durante attenuazione (0-100%)",
    lbl_lang:"Lingua interfaccia",lbl_admin_pw:"Password impostazioni",
    lbl_device_id:"ID dispositivo",btn_ota:"Aggiornamento OTA",
    note_dim:"Esempio: inizio=22, fine=7 attenua dalle 22 alle 7.",
    note_https:"Nota: Il server web usa HTTP. Il traffico resta sulla rete locale.",
    help_low:"Il livello di CO\u2082 sotto questo viene mostrato in verde.",
    help_med:"Il livello di CO\u2082 sotto questo viene mostrato in giallo.",
    help_alert:"Il livello di CO\u2082 uguale o superiore attiva un allarme.",
    help_device_id:"Identificatore inviato con i dati cloud e MQTT.",
    help_admin_pw:"Lasciare vuoto per disabilitare la protezione con password.",
    help_sta:"Inserisci le credenziali Wi-Fi per connettere il dispositivo.",
    help_cloud:"Invia le letture di CO\u2082 al pannello Know CO\u2082.",
    help_mqtt:"Pubblica le letture su un broker MQTT locale (es. Home Assistant).",
    help_aio:"Pubblica le letture su feed di Adafruit IO.",
    help_dim:"Riduce automaticamente la luminosita nelle ore configurate. Richiede NTP.",
    help_ota:"Scarica e installa nuovo firmware. Il dispositivo si riavviera.",
    lbl_regen:"Rigenera credenziali AP",lbl_sta_connect:"Connetti al prossimo riavvio",
    lbl_mode_text:"Testo",lbl_mode_big:"CO\u2082 grande",lbl_mode_graph:"Grafico",
    lbl_scale_fixed:"400\u20132000 ppm (stretto)",lbl_scale_wide:"400\u20133000 ppm (ampio)",lbl_scale_auto:"Automatico",
    lbl_max_pts:"Buffer storico (campioni)",lbl_cloud_secret:"Segreto/token del dispositivo",
    lbl_mqtt_port:"Porta",lbl_mqtt_user:"Utente (opzionale)",lbl_mqtt_pass:"Password (opzionale)",
    lbl_mqtt_prefix:"Prefisso topic",lbl_mqtt_interval:"Intervallo di pubblicazione (secondi)",
    lbl_aio_group:"Chiave gruppo feed",lbl_aio_interval:"Intervallo di pubblicazione (secondi)",
    nav_calib:"Calibrazione",nav_back:"Torna alle Impostazioni",nav_ota:"Aggiornamento firmware",
    ph_device_id:"co2-nodo-1",ph_sta_ssid:"Nome della tua rete Wi-Fi",ph_sta_pass:"Password Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Incolla il tuo token qui",
    ph_mqtt_broker:"192.168.1.x o mqtt.esempio.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"tuo-utente-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Attiva o disattiva gli avvisi CO\u2082",
    aria_cloud_check:"Attiva o disattiva il caricamento cloud",
    aria_mqtt_check:"Attiva o disattiva la pubblicazione MQTT",
    aria_aio_check:"Attiva o disattiva la pubblicazione Adafruit IO",
    aria_dim_check:"Attiva o disattiva l'attenuazione programmata",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Vai al contenuto principale"
  },
  ja: {
    title:"Know CO\u2082 \u8a2d\u5b9a",save:"\u8a2d\u5b9a\u3092\u4fdd\u5b58",
    sec_display:"\u8868\u793a\u3068\u3057\u304d\u3044\u5024",sec_wifi:"Wi-Fi",
    sec_cloud:"\u30af\u30e9\u30a6\u30c9\u30a2\u30c3\u30d7\u30ed\u30fc\u30c9",sec_mqtt:"MQTT\u30d6\u30ed\u30fc\u30ab\u30fc",
    sec_aio:"Adafruit IO",sec_dim:"\u753b\u9762\u8abf\u5149",
    sec_device:"\u30c7\u30d0\u30a4\u30b9",sec_calib:"\u30ad\u30e3\u30ea\u30d6\u30ec\u30fc\u30b7\u30e7\u30f3",
    lbl_low:"\u4f4e\u3057\u304d\u3044\u5024 (ppm)",lbl_med:"\u4e2d\u3057\u304d\u3044\u5024 (ppm)",
    lbl_alert:"\u8b66\u544a\u3057\u304d\u3044\u5024 (ppm)",lbl_temp:"\u6e29\u5ea6\u5358\u4f4d",
    lbl_alerts:"\u30a2\u30e9\u30fc\u30c8\u3092\u6709\u52b9\u306b\u3059\u308b",lbl_scale:"\u30b0\u30e9\u30d5\u30b9\u30b1\u30fc\u30eb",
    lbl_ap_ssid:"AP\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u540d",lbl_ap_pass:"AP\u30d1\u30b9\u30ef\u30fc\u30c9",
    lbl_sta_ssid:"\u30db\u30fcWi-Fi SSID",lbl_sta_pass:"Wi-Fi\u30d1\u30b9\u30ef\u30fc\u30c9",
    lbl_cloud_url:"API URL",lbl_cloud_token:"\u30c7\u30d0\u30a4\u30b9\u30c8\u30fc\u30af\u30f3",
    lbl_cloud_interval:"\u9001\u4fe1\u9593\u9694\uff08\u79d2\uff09",lbl_cloud_en:"\u30af\u30e9\u30a6\u30c9\u30a2\u30c3\u30d7\u30ed\u30fc\u30c9\u3092\u6709\u52b9\u306b\u3059\u308b",
    lbl_mqtt_en:"MQTT\u914d\u4fe1\u3092\u6709\u52b9\u306b\u3059\u308b",lbl_mqtt_broker:"\u30d6\u30ed\u30fc\u30ab\u30fc\u30db\u30b9\u30c8/IP",
    lbl_aio_en:"Adafruit IO\u3092\u6709\u52b9\u306b\u3059\u308b",lbl_aio_user:"AIO\u30e6\u30fc\u30b6\u30fc\u540d",
    lbl_dim_en:"\u30b9\u30b1\u30b8\u30e5\u30fc\u30eb\u8abf\u5149\u3092\u6709\u52b9\u306b\u3059\u308b\uff08NTP\u5fc5\u8981\uff09",
    lbl_dim_start:"\u958b\u59cb\u6642\u523b (0-23)",lbl_dim_end:"\u7d42\u4e86\u6642\u523b (0-23)",
    lbl_dim_bright:"\u8abf\u5149\u4e2d\u306e\u8f1d\u5ea6 (0-100%)",
    lbl_lang:"\u30a4\u30f3\u30bf\u30fc\u30d5\u30a7\u30fc\u30b9\u8a00\u8a9e",lbl_admin_pw:"\u8a2d\u5b9a\u30d1\u30b9\u30ef\u30fc\u30c9",
    lbl_device_id:"\u30c7\u30d0\u30a4\u30b9ID",btn_ota:"OTA\u30d5\u30a1\u30fc\u30e0\u30a6\u30a7\u30a2\u66f4\u65b0",
    note_dim:"\u4f8b: \u958b\u59cb=22, \u7d42\u4e86=7\u306f22\u664200\u5206\u304b\u307a7\u664200\u5206\u307e\u3067\u8abf\u5149\u3002",
    note_https:"\u6ce8\u610f: \u30ed\u30fc\u30ab\u30eb\u30b5\u30fc\u30d0\u30fc\u306fHTTP\u3092\u4f7f\u7528\u3002\u901a\u4fe1\u306fLAN\u5185\u306b\u3068\u3069\u307e\u308a\u307e\u3059\u3002",
    help_low:"CO\u2082\u30ec\u30d9\u30eb\u304c\u3053\u308c\u4ee5\u4e0b\u306e\u5834\u5408\u7dd1\u8272\u3067\u8868\u793a\u3002",
    help_med:"CO\u2082\u30ec\u30d9\u30eb\u304c\u3053\u308c\u4ee5\u4e0b\u306e\u5834\u5408\u9ec4\u8272\u3067\u8868\u793a\u3002",
    help_alert:"\u3053\u306e\u5024\u4ee5\u4e0a\u306eCO\u2082\u30ec\u30d9\u30eb\u3067\u30a2\u30e9\u30fc\u30c8\u3002",
    help_device_id:"\u30af\u30e9\u30a6\u30c9\u304a\u3088\u3073MQTT\u30c7\u30fc\u30bf\u306b\u9644\u52a0\u3055\u308c\u308b\u8b58\u5225\u5b50\u3002",
    help_admin_pw:"\u30d1\u30b9\u30ef\u30fc\u30c9\u3092\u7121\u52b9\u306b\u3059\u308b\u306b\u306f\u7a7a\u767d\u306b\u3057\u3066\u304f\u3060\u3055\u3044\u3002",
    help_sta:"Wi-Fi\u8cc7\u683c\u60c5\u5831\u3092\u5165\u529b\u3057\u3066\u30c7\u30d0\u30a4\u30b9\u3092\u63a5\u7d9a\u3002",
    help_cloud:"CO\u2082\u8a08\u6e2c\u5024\u3092Know CO\u2082\u30c0\u30c3\u30b7\u30e5\u30dc\u30fc\u30c9\u306b\u9001\u4fe1\u3002",
    help_mqtt:"\u30ed\u30fc\u30ab\u30ebMQTT\u30d6\u30ed\u30fc\u30ab\u30fc\u306b\u8a08\u6e2c\u5024\u3092\u9001\u4fe1\u3002",
    help_aio:"Adafruit IO\u30d5\u30a3\u30fc\u30c9\u306b\u8a08\u6e2c\u5024\u3092\u9001\u4fe1\u3002",
    help_dim:"\u8a2d\u5b9a\u6642\u9593\u306b\u81ea\u52d5\u3067\u753b\u9762\u3092\u6697\u304f\u3059\u308b\u3002NTP\u5fc5\u8981\u3002",
    help_ota:"\u65b0\u3057\u3044\u30d5\u30a1\u30fc\u30e0\u30a6\u30a7\u30a2\u3092\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9\u3057\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb\u3002",
    lbl_regen:"AP\u8cc7\u683c\u60c5\u5831\u3092\u518d\u751f\u6210",lbl_sta_connect:"\u6b21\u306e\u518d\u8d77\u52d5\u6642\u306b\u63a5\u7d9a",
    lbl_mode_text:"\u30c6\u30ad\u30b9\u30c8",lbl_mode_big:"\u5927\u304dCO\u2082",lbl_mode_graph:"\u30b0\u30e9\u30d5",
    lbl_scale_fixed:"400\u20132000 ppm (\u9577\u65b9\u5f62)",lbl_scale_wide:"400\u20133000 ppm (\u5e83\u3044)",lbl_scale_auto:"\u81ea\u52d5",
    lbl_max_pts:"\u5c65\u6b74\u30d0\u30c3\u30d5\u30a1\uff08\u30b5\u30f3\u30d7\u30eb\uff09",lbl_cloud_secret:"\u30c7\u30d0\u30a4\u30b9\u30b7\u30fc\u30af\u30ec\u30c3\u30c8/\u30c8\u30fc\u30af\u30f3",
    lbl_mqtt_port:"\u30dd\u30fc\u30c8",lbl_mqtt_user:"\u30e6\u30fc\u30b6\u30fc\uff08\u4efb\u610f\uff09",lbl_mqtt_pass:"\u30d1\u30b9\u30ef\u30fc\u30c9\uff08\u4efb\u610f\uff09",
    lbl_mqtt_prefix:"\u30c8\u30d4\u30c3\u30af\u30d7\u30ec\u30d5\u30a3\u30c3\u30af\u30b9",lbl_mqtt_interval:"\u9001\u4fe1\u9593\u9694\uff08\u79d2\uff09",
    lbl_aio_group:"\u30d5\u30a3\u30fc\u30c9\u30b0\u30eb\u30fc\u30d7\u30ad\u30fc",lbl_aio_interval:"\u9001\u4fe1\u9593\u9694\uff08\u79d2\uff09",
    nav_calib:"\u30ad\u30e3\u30ea\u30d6\u30ec\u30fc\u30b7\u30e7\u30f3",nav_back:"\u8a2d\u5b9a\u306b\u623b\u308b",nav_ota:"\u30d5\u30a1\u30fc\u30e0\u30a6\u30a7\u30a2\u66f4\u65b0",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Wi-Fi\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u540d",ph_sta_pass:"Wi-Fi\u30d1\u30b9\u30ef\u30fc\u30c9",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"\u30c8\u30fc\u30af\u30f3\u3092\u8cbc\u308a\u4ed8\u3051",
    ph_mqtt_broker:"192.168.1.x \u307e\u305f\u306f mqtt.\u4f8b\u3002com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"adafruit\u30e6\u30fc\u30b6\u30fc\u540d",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"CO\u2082\u8b66\u544a\u901a\u77e5\u306e\u5207\u308a\u66ff\u3048",
    aria_cloud_check:"\u30af\u30e9\u30a6\u30c9\u30c7\u30fc\u30bf\u9001\u4fe1\u306e\u5207\u308a\u66ff\u3048",
    aria_mqtt_check:"MQTT\u914d\u4fe1\u306e\u5207\u308a\u66ff\u3048",
    aria_aio_check:"Adafruit IO\u914d\u4fe1\u306e\u5207\u308a\u66ff\u3048",
    aria_dim_check:"\u30b9\u30b1\u30b8\u30e5\u30fc\u30eb\u8abf\u5149\u306e\u5207\u308a\u66ff\u3048",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"\u30e1\u30a4\u30f3\u30b3\u30f3\u30c6\u30f3\u30c4\u306b\u30b9\u30ad\u30c3\u30d7"
  },
  zh: {
    title:"Know CO\u2082 \u8bbe\u7f6e",save:"\u4fdd\u5b58\u8bbe\u7f6e",
    sec_display:"\u663e\u793a\u4e0e\u9608\u503c",sec_wifi:"Wi-Fi",
    sec_cloud:"\u4e91\u4e0a\u4f20",sec_mqtt:"MQTT\u4e2d\u7ee7\u5668",
    sec_aio:"Adafruit IO",sec_dim:"\u5c4f\u5e55\u8c03\u5149",
    sec_device:"\u8bbe\u5907",sec_calib:"\u6821\u51c6",
    lbl_low:"\u4f4e\u9608\u503c (ppm)",lbl_med:"\u4e2d\u9608\u503c (ppm)",
    lbl_alert:"\u8b66\u62a5\u9608\u503c (ppm)",lbl_temp:"\u6e29\u5ea6\u5355\u4f4d",
    lbl_alerts:"\u542f\u7528\u8b66\u62a5",lbl_scale:"\u56fe\u8868\u5c3a\u5ea6",
    lbl_ap_ssid:"AP\u7f51\u7edc\u540d\u79f0",lbl_ap_pass:"AP\u5bc6\u7801",
    lbl_sta_ssid:"\u5bb6\u5ead Wi-Fi SSID",lbl_sta_pass:"Wi-Fi\u5bc6\u7801",
    lbl_cloud_url:"API \u5730\u5740",lbl_cloud_token:"\u8bbe\u5907\u4ee4\u724c",
    lbl_cloud_interval:"\u4e0a\u4f20\u95f4\u9694\uff08\u79d2\uff09",lbl_cloud_en:"\u542f\u7528\u4e91\u4e0a\u4f20",
    lbl_mqtt_en:"\u542f\u7528 MQTT \u53d1\u5e03",lbl_mqtt_broker:"\u4e2d\u7ee7\u5668\u4e3b\u673a/IP",
    lbl_aio_en:"\u542f\u7528 Adafruit IO",lbl_aio_user:"AIO \u7528\u6237\u540d",
    lbl_dim_en:"\u542f\u7528\u5b9a\u65f6\u8c03\u5149\uff08\u9700\u8981 NTP\uff09",
    lbl_dim_start:"\u5f00\u59cb\u65f6\u95f4 (0-23)",lbl_dim_end:"\u7ed3\u675f\u65f6\u95f4 (0-23)",
    lbl_dim_bright:"\u8c03\u5149\u671f\u95f4\u4eae\u5ea6 (0-100%)",
    lbl_lang:"\u754c\u9762\u8bed\u8a00",lbl_admin_pw:"\u8bbe\u7f6e\u5bc6\u7801",
    lbl_device_id:"\u8bbe\u5907 ID",btn_ota:"OTA \u56fa\u4ef6\u66f4\u65b0",
    note_dim:"\u793a\u4f8b\uff1a\u5f00\u59cb=22, \u7ed3\u675f=7 \u5c06\u4ece22\u70b9\u8c03\u5149\u521307\u70b9\u3002",
    note_https:"\u6ce8\u610f\uff1a\u672c\u5730\u670d\u52a1\u5668\u4f7f\u7528 HTTP\u3002\u6d41\u91cf\u4ec5\u5728\u5c40\u57df\u7f51\u5185\u3002",
    help_low:"CO\u2082\u6c34\u5e73\u4f4e\u4e8e\u6b64\u503c\u65f6\u663e\u793a\u7eff\u8272\u3002",
    help_med:"CO\u2082\u6c34\u5e73\u4f4e\u4e8e\u6b64\u503c\u65f6\u663e\u793a\u9ec4\u8272\u3002",
    help_alert:"CO\u2082\u6c34\u5e73\u8fbe\u5230\u6216\u8d85\u8fc7\u6b64\u503c\u65f6\u89e6\u53d1\u8b66\u62a5\u3002",
    help_device_id:"\u968f\u4e91\u548c MQTT \u6570\u636e\u4e00\u8d77\u53d1\u9001\u7684\u6807\u8bc6\u7b26\u3002",
    help_admin_pw:"\u7559\u7a7a\u53ef\u7981\u7528\u5bc6\u7801\u4fdd\u62a4\u3002",
    help_sta:"\u8f93\u5165 Wi-Fi \u51ed\u636e\u4ee5\u8fde\u63a5\u8bbe\u5907\u3002",
    help_cloud:"\u5c06 CO\u2082 \u8bfb\u6570\u53d1\u9001\u5230 Know CO\u2082 \u4e91\u5e73\u53f0\u3002",
    help_mqtt:"\u5c06\u8bfb\u6570\u53d1\u5e03\u5230\u672c\u5730 MQTT \u4e2d\u7ee7\u5668\uff08\u5982 Home Assistant\uff09\u3002",
    help_aio:"\u5c06\u8bfb\u6570\u53d1\u5e03\u5230 Adafruit IO \u9879\u76ee\u3002",
    help_dim:"\u5728\u8bbe\u5b9a\u65f6\u95f4\u81ea\u52a8\u964d\u4f4e\u4eae\u5ea6\u3002\u9700\u8981 NTP\u3002",
    help_ota:"\u4e0b\u8f7d\u5e76\u5b89\u88c5\u65b0\u56fa\u4ef6\u3002\u8bbe\u5907\u5c06\u91cd\u542f\u3002",
    lbl_regen:"\u91cd\u65b0\u751f\u6210 AP \u51ed\u636e",lbl_sta_connect:"\u4e0b\u6b21\u91cd\u542f\u65f6\u8fde\u63a5",
    lbl_mode_text:"\u6587\u5b57",lbl_mode_big:"\u5927\u5b57 CO\u2082",lbl_mode_graph:"\u56fe\u8868",
    lbl_scale_fixed:"400\u20132000 ppm\uff08\u7d27\u51d1\uff09",lbl_scale_wide:"400\u20133000 ppm\uff08\u5bbd\u677e\uff09",lbl_scale_auto:"\u81ea\u52a8",
    lbl_max_pts:"\u5386\u53f2\u7f13\u51b2\u533a\uff08\u6837\u672c\uff09",lbl_cloud_secret:"\u8bbe\u5907\u5bc6\u94a5/\u4ee4\u724c",
    lbl_mqtt_port:"\u7aef\u53e3",lbl_mqtt_user:"\u7528\u6237\u540d\uff08\u53ef\u9009\uff09",lbl_mqtt_pass:"\u5bc6\u7801\uff08\u53ef\u9009\uff09",
    lbl_mqtt_prefix:"\u4e3b\u9898\u524d\u7f00",lbl_mqtt_interval:"\u53d1\u5e03\u95f4\u9694\uff08\u79d2\uff09",
    lbl_aio_group:"\u9879\u76ee\u7ec4\u5bc6\u94a5",lbl_aio_interval:"\u53d1\u5e03\u95f4\u9694\uff08\u79d2\uff09",
    nav_calib:"\u6821\u51c6",nav_back:"\u8fd4\u56de\u8bbe\u7f6e",nav_ota:"\u56fa\u4ef6\u66f4\u65b0",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Wi-Fi \u7f51\u7edc\u540d\u79f0",ph_sta_pass:"Wi-Fi \u5bc6\u7801",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"\u5c06\u4ee4\u724c\u7c98\u8d34\u5230\u8fd9\u91cc",
    ph_mqtt_broker:"192.168.1.x \u6216 mqtt.\u793a\u4f8b.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"adafruit\u7528\u6237\u540d",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"\u5207\u6362 CO\u2082 \u8b66\u62a5\u901a\u77e5",
    aria_cloud_check:"\u5207\u6362\u4e91\u6570\u636e\u4e0a\u4f20",
    aria_mqtt_check:"\u5207\u6362 MQTT \u53d1\u5e03",
    aria_aio_check:"\u5207\u6362 Adafruit IO \u53d1\u5e03",
    aria_dim_check:"\u5207\u6362\u5b9a\u65f6\u8c03\u5149",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"\u8df3\u81f3\u4e3b\u8981\u5185\u5bb9"
  },
  ko: {
    title:"Know CO\u2082 \uc124\uc815",save:"\uc124\uc815 \uc800\uc7a5",
    sec_display:"\ub514\uc2a4\ud50c\ub808\uc774 \ubc0f \uc784\uacc4\uac12",sec_wifi:"Wi-Fi",
    sec_cloud:"\ud074\ub77c\uc6b0\ub4dc \uc5c5\ub85c\ub4dc",sec_mqtt:"MQTT \ube0c\ub85c\ucee4",
    sec_aio:"Adafruit IO",sec_dim:"\ud654\uba74 \ubc1d\uae30 \uc608\uc57d",
    sec_device:"\uc7a5\uce58",sec_calib:"\ubcf4\uc815",
    lbl_low:"\ub099\uc740 \uc784\uacc4\uac12 (ppm)",lbl_med:"\uc911\uac04 \uc784\uacc4\uac12 (ppm)",
    lbl_alert:"\uacbd\ubcf4 \uc784\uacc4\uac12 (ppm)",lbl_temp:"\uc628\ub3c4 \ub2e8\uc704",
    lbl_alerts:"\uacbd\ubcf4 \ud65c\uc131\ud654",lbl_scale:"\uadf8\ub798\ud504 \ucca0\ub3c4",
    lbl_ap_ssid:"AP \ub124\ud2b8\uc6cc\ud06c \uc774\ub984",lbl_ap_pass:"AP \ube44\ubc00\ubc88\ud638",
    lbl_sta_ssid:"\ud648 Wi-Fi SSID",lbl_sta_pass:"Wi-Fi \ube44\ubc00\ubc88\ud638",
    lbl_cloud_url:"API URL",lbl_cloud_token:"\uc7a5\uce58 \ud1a0\ud070",
    lbl_cloud_interval:"\uc5c5\ub85c\ub4dc \uac04\uaca9 (\ucd08)",lbl_cloud_en:"\ud074\ub77c\uc6b0\ub4dc \uc5c5\ub85c\ub4dc \ud65c\uc131\ud654",
    lbl_mqtt_en:"MQTT \uac8c\uc2dc \ud65c\uc131\ud654",lbl_mqtt_broker:"\ube0c\ub85c\ucee4 \ud638\uc2a4\ud2b8/IP",
    lbl_aio_en:"Adafruit IO \ud65c\uc131\ud654",lbl_aio_user:"AIO \uc0ac\uc6a9\uc790\uba85",
    lbl_dim_en:"\uc608\uc57d \ubc1d\uae30 \uc870\uc808 \ud65c\uc131\ud654 (NTP \ud544\uc694)",
    lbl_dim_start:"\uc2dc\uc791 \uc2dc\uac04 (0-23)",lbl_dim_end:"\uc885\ub8cc \uc2dc\uac04 (0-23)",
    lbl_dim_bright:"\uc5b4\ub450\uc6b4 \uc2dc\uac04 \ubc1d\uae30 (0-100%)",
    lbl_lang:"\uc778\ud130\ud398\uc774\uc2a4 \uc5b8\uc5b4",lbl_admin_pw:"\uc124\uc815 \ube44\ubc00\ubc88\ud638",
    lbl_device_id:"\uc7a5\uce58 ID",btn_ota:"OTA \ud3fc\uc6e8\uc5b4 \uc5c5\ub370\uc774\ud2b8",
    note_dim:"\uc608: \uc2dc\uc791=22, \uc885\ub8cc=7\uc740 \uc624\ud6c4 10\uc2dc\ubd80\ud130 \uc624\uc804 7\uc2dc\uae4c\uc9c0 \uc870\uc808.",
    note_https:"\ucc38\uace0: \ub85c\ucec8 \uc11c\ubc84\ub294 HTTP\ub97c \uc0ac\uc6a9\ud569\ub2c8\ub2e4. \ud2b8\ub798\ud53d\uc740 LAN \ub0b4\uc5d0\ub9e8 \uc788\uc2b5\ub2c8\ub2e4.",
    help_low:"CO\u2082 \uc218\uc900\uc774 \uc774 \uac12 \uc774\ud558\uc774\uba74 \ub179\uc0c9\uc73c\ub85c \ud45c\uc2dc\ub429\ub2c8\ub2e4.",
    help_med:"CO\u2082 \uc218\uc900\uc774 \uc774 \uac12 \uc774\ud558\uc774\uba74 \ub178\ub780\uc0c9\uc73c\ub85c \ud45c\uc2dc\ub429\ub2c8\ub2e4.",
    help_alert:"\uc774 \uac12 \uc774\uc0c1\uc758 CO\u2082 \uc218\uc900\uc5d0\uc11c \uacbd\ubcf4\uac00 \ud65c\uc131\ud654\ub429\ub2c8\ub2e4.",
    help_device_id:"\ud074\ub77c\uc6b0\ub4dc \ubc0f MQTT \ub370\uc774\ud130\uc640 \ud568\uaed8 \uc804\uc1a1\ub418\ub294 \uc2dd\ubcc4\uc790.",
    help_admin_pw:"\ube44\ubc00\ubc88\ud638 \ubcf4\ud638\ub97c \ube44\ud65c\uc131\ud654\ud558\ub824\uba74 \ube44\uc6cc\ub450\uc138\uc694.",
    help_sta:"Wi-Fi \uc790\uaca9 \uc99d\uba85\uc744 \uc785\ub825\ud558\uc5ec \uc7a5\uce58\ub97c \uc5f0\uacb0\ud558\uc138\uc694.",
    help_cloud:"CO\u2082 \uce21\uc815\uac12\uc744 Know CO\u2082 \ud074\ub77c\uc6b0\ub4dc\ub85c \uc804\uc1a1\ud569\ub2c8\ub2e4.",
    help_mqtt:"\ub85c\uce7c MQTT \ube0c\ub85c\ucee4\uc5d0 \uce21\uc815\uac12\uc744 \uac8c\uc2dc\ud569\ub2c8\ub2e4.",
    help_aio:"Adafruit IO \ud53c\ub4dc\uc5d0 \uce21\uc815\uac12\uc744 \uac8c\uc2dc\ud569\ub2c8\ub2e4.",
    help_dim:"\uc124\uc815\ub41c \uc2dc\uac04\ub300\uc5d0 \uc790\ub3d9\uc73c\ub85c \ud654\uba74 \ubc1d\uae30\ub97c \uc904\uc785\ub2c8\ub2e4. NTP \ud544\uc694.",
    help_ota:"\uc0c8 \ud3fc\uc6e8\uc5b4\ub97c \ub2e4\uc6b4\ub85c\ub4dc\ud558\uace0 \uc124\uce58\ud569\ub2c8\ub2e4. \uc7a5\uce58\uac00 \uc7ac\ubd80\ud305\ub429\ub2c8\ub2e4.",
    lbl_regen:"AP \uc790\uaca9 \uc99d\uba85 \uc7ac\uc0dd\uc131",lbl_sta_connect:"\ub2e4\uc74c \uc7ac\ubd80\ud305 \uc2dc \uc5f0\uacb0",
    lbl_mode_text:"\ud14d\uc2a4\ud2b8",lbl_mode_big:"\ub300\ud615 CO\u2082",lbl_mode_graph:"\uadf8\ub798\ud504",
    lbl_scale_fixed:"400\u20132000 ppm (\uc9e7\uc740)",lbl_scale_wide:"400\u20133000 ppm (\ub113\uc740)",lbl_scale_auto:"\uc790\ub3d9",
    lbl_max_pts:"\ud788\uc2a4\ud1a0\ub9ac \ubc84\ud37c (\uc0d8\ud50c)",lbl_cloud_secret:"\uc7a5\uce58 \ube44\ubc00/\ud1a0\ud070",
    lbl_mqtt_port:"\ud3ec\ud2b8",lbl_mqtt_user:"\uc0ac\uc6a9\uc790 (\uc120\ud0dd)",lbl_mqtt_pass:"\ube44\ubc00\ubc88\ud638 (\uc120\ud0dd)",
    lbl_mqtt_prefix:"\ud1a0\ud53d \uc811\ub450\uc0ac",lbl_mqtt_interval:"\uac8c\uc2dc \uac04\uaca9 (\ucd08)",
    lbl_aio_group:"\ud53c\ub4dc \uadf8\ub8f9 \ud0a4",lbl_aio_interval:"\uac8c\uc2dc \uac04\uaca9 (\ucd08)",
    nav_calib:"\ubcf4\uc815",nav_back:"\uc124\uc815\uc73c\ub85c \ub3cc\uc544\uac00\uae30",nav_ota:"\ud3fc\uc6e8\uc5b4 \uc5c5\ub370\uc774\ud2b8",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Wi-Fi \ub124\ud2b8\uc6cc\ud06c \uc774\ub984",ph_sta_pass:"Wi-Fi \ube44\ubc00\ubc88\ud638",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"\ud1a0\ud070\uc744 \uc5ec\uae30\uc5d0 \ubd99\uc5ec\ub123\uc73c\uc138\uc694",
    ph_mqtt_broker:"192.168.1.x \ub610\ub294 mqtt.\uc608\uc2dc.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"adafruit \uc0ac\uc6a9\uc790\uba85",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"CO\u2082 \uacbd\ubcf4 \uc54c\ub9bc \ud1a0\uae00",
    aria_cloud_check:"\ud074\ub77c\uc6b0\ub4dc \ub370\uc774\ud130 \uc5c5\ub85c\ub4dc \ud1a0\uae00",
    aria_mqtt_check:"MQTT \uac8c\uc2dc \ud1a0\uae00",
    aria_aio_check:"Adafruit IO \uac8c\uc2dc \ud1a0\uae00",
    aria_dim_check:"\uc608\uc57d \ubc1d\uae30 \uc870\uc808 \ud1a0\uae00",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"\ubcf8\ubb38\uc73c\ub85c \uac74\ub108\ubf00\uae30"
  }
};
function applyLang(lang){
  var t = T[lang] || T['en'];
  // Update the document language for screen readers
  document.documentElement.lang = lang;
  // Update the page <title>
  if (t.title) document.title = t.title;
  // Update text nodes
  document.querySelectorAll('[data-i18n]').forEach(function(el){
    var k = el.getAttribute('data-i18n');
    if (!t[k]) return;
    if (el.children.length === 0) {
      el.textContent = t[k];
    } else {
      for (var i = 0; i < el.childNodes.length; i++) {
        if (el.childNodes[i].nodeType === 3) {
          el.childNodes[i].nodeValue = t[k];
          break;
        }
      }
    }
  });
  // Update placeholder attributes
  document.querySelectorAll('[data-i18n-placeholder]').forEach(function(el){
    var k = el.getAttribute('data-i18n-placeholder');
    if (t[k]) el.placeholder = t[k];
  });
  // Update aria-label attributes
  document.querySelectorAll('[data-i18n-aria]').forEach(function(el){
    var k = el.getAttribute('data-i18n-aria');
    if (t[k]) el.setAttribute('aria-label', t[k]);
  });
  // Keep language selector in sync
  var sel = document.querySelector('select[name=lang]');
  if (sel) sel.value = lang;
  localStorage.setItem('kco2_lang', lang);
}
(function(){
  var saved = localStorage.getItem('kco2_lang') || '""" + current_lang + """';
  var sel = document.querySelector('select[name=lang]');
  if (sel) sel.addEventListener('change', function(){ applyLang(this.value); });
  applyLang(saved);
})();
</script>
</body>
</html>"""
    return html

def handle_data_route(conn):
    data_points = co2_history[-MAX_WEB_POINTS:]
    ints = []
    for v in data_points:
        if isinstance(v, (int, float)):
            iv = _as_int(v)
            if iv is not None:
                ints.append(iv)

    header, body = make_json_response({"co2": ints})
    send_all(conn, header)
    send_all(conn, body)

def handle_export_csv_route(conn):
    """Return the in-RAM CO2 history as a downloadable CSV file."""
    try:
        import rtc as _rtc
        now_ts = int(time.time())
    except Exception:
        now_ts = 0

    rows = ["seconds_ago,co2_ppm,temp_c,rh_pct"]
    pts = co2_history[-MAX_WEB_POINTS:]
    total = len(pts)
    for i, v in enumerate(pts):
        # Estimate when this sample was taken, working backwards from now.
        age_s = int((total - 1 - i) * SCD_MEASUREMENT_PERIOD)
        co2_val = int(v) if v is not None else ""
        # temp and rh are only available as the latest values, so omit for history
        rows.append("%d,%s,," % (age_s, co2_val))
    # Overwrite the last row with the most recent temp/rh if available
    if total > 0 and last_temp_c is not None and last_rh is not None:
        rows[-1] = "0,%s,%.1f,%.1f" % (
            int(pts[-1]) if pts[-1] is not None else "",
            last_temp_c,
            last_rh
        )
    csv_body = "\r\n".join(rows) + "\r\n"
    csv_bytes = csv_body.encode("utf-8")
    header = build_response(
        200,
        "text/csv; charset=utf-8",
        csv_bytes
    )[0]
    # Add Content-Disposition to trigger download in browser
    header = header.replace(
        b"\r\n\r\n",
        b"\r\nContent-Disposition: attachment; filename=\"knowco2_export.csv\"\r\n\r\n",
        1
    )
    send_all(conn, header)
    send_all(conn, csv_bytes)

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
        "firmware_version": FIRMWARE_VERSION,
        "cp_version": cp_version_str,

        "battery_v": vbat,
        "battery_pct": pct,
        "battery_gauge": fuel_gauge_kind,
        "battery_bus": fuel_bus_name,

        "wifi_mode": wifi_mode,
        "fs_readonly": FS_READONLY,
        "ip": ip_str_cached,
        "mdns": (mdns_hostname + ".local") if (wifi_mode == WIFI_MODE_STA and mdns_hostname) else None,

        "cloud_enabled": cloud_enabled,
        "cloud_interval_sec": cloud_interval_sec,
        "cloud_configured": bool(cloud_api_url) and bool(cloud_device_token),
        "cloud_last_attempt_ts": cloud_last_attempt_ts,
        "cloud_last_http": cloud_last_http,
        "cloud_last_error": cloud_last_error,
        # Instantaneous CO₂ rate of change (ppm per second)
        "rate_of_change": rate_of_change,
    }

    # Diagnostics (memory + uptime)
    payload["energy_mode"] = energy_mode
    payload["scd_period_effective"] = _scd_period_effective

    try:
        payload["uptime_s"] = int(time.monotonic() - BOOT_TIME_MONO)
    except Exception:
        pass
    try:
        payload["mem_free"] = gc.mem_free()
        payload["mem_alloc"] = gc.mem_alloc()
        payload["mem_free_min"] = mem_free_min if mem_samples else None
        payload["mem_free_max"] = mem_free_max if mem_samples else None
        payload["mem_free_ema"] = int(mem_free_ema) if mem_samples else None
        payload["mem_samples"] = mem_samples
        payload["last_gc_s_ago"] = int(time.monotonic() - last_gc_ts) if last_gc_ts else None
    except Exception:
        pass

    # Include sensor staleness diagnostics in the status.  Report
    # how many seconds have elapsed since the last successful SCD4x
    # reading and a boolean flag indicating whether the sensor is
    # considered "OK" (i.e., fresh data within the timeout).
    try:
        _age = time.monotonic() - last_scd_sample_ts
        payload["last_sensor_sample_s"] = int(_age)
        payload["sensor_ok"] = (_age <= SCD_SAMPLE_TIMEOUT)
    except Exception:
        # In case of errors computing staleness, leave the fields unset
        pass

    # Include calibration settings in the status.  Expose the current
    # Automatic Self Calibration (ASC) state, altitude (m), ambient pressure (hPa),
    # and details of the last manual calibration.  If any values are missing,
    # defaults are provided.  Wrap in a try/except to avoid crashing the
    # status handler if settings are malformed.
    try:
        payload["asc_enabled"] = bool(settings.get("asc_enabled", True))
        payload["altitude"] = int(settings.get("altitude", 0) or 0)
        payload["ambient_pressure"] = int(settings.get("ambient_pressure", 0) or 0)
        payload["last_calibration_ts"] = settings.get("last_calibration_ts", 0)
        payload["last_calibration_ref"] = settings.get("last_calibration_ref", 0)
    except Exception:
        pass

    header, body = make_json_response(payload)
    send_all(conn, header)
    send_all(conn, body)


def render_calibration_page(authed_pw=""):
    """
    Generate the HTML for the calibration page.  This page allows the user
    to configure altitude and ambient pressure compensation, toggle
    Automatic Self Calibration (ASC), and perform a manual forced
    calibration against a reference CO₂ concentration.  It also
    displays the current calibration settings and the timestamp of the
    last calibration.

    authed_pw: if non-empty, embeds a hidden 'pw' field in the form so the
    password is preserved across GET submissions when auth is enabled.
    """
    asc_enabled = bool(settings.get("asc_enabled", True))
    asc_checked = "checked" if asc_enabled else ""
    altitude = settings.get("altitude", 0)
    pressure = settings.get("ambient_pressure", 0)
    last_ts = settings.get("last_calibration_ts", 0)
    last_ref = settings.get("last_calibration_ref", 0)
    # Format the last calibration timestamp as a human‑readable string if possible
    if last_ts:
        try:
            lt = time.localtime(last_ts)
            last_ts_str = "%04d-%02d-%02d %02d:%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min)
        except Exception:
            last_ts_str = str(int(last_ts))
    else:
        last_ts_str = "Never"
    # Escape numeric values for safety
    def esc(val):
        try:
            return str(val)
        except Exception:
            return ""

    # Build a calibration text string before building the HTML.  If a
    # calibration reference exists, append " ppm"; otherwise show "None".  We
    # compute this once to avoid inline conditional logic in the HTML string,
    # which can lead to confusing operator precedence.
    calibration_text = (esc(last_ref) + " ppm") if last_ref else "None"
    # If the caller supplied a validated password, embed it as a hidden field so
    # the form preserves auth across multiple GET submissions.
    if authed_pw:
        _esc_pw = authed_pw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        calibration_pw_field = "<input type=\"hidden\" name=\"pw\" value=\"" + _esc_pw + "\">\n"
    else:
        calibration_pw_field = ""
    # Build the HTML page.  Use similar styling to the settings page for
    # consistency.  This page uses GET to submit form data so that
    # parameters appear in the query string for simple parsing.
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
    """
    Handle HTTP requests to the /calibration route.  Accepts GET parameters
    to update altitude, ambient pressure, ASC mode, and to perform a
    forced calibration.  After applying any changes, persists the new
    settings and reconfigures the sensor, then renders the calibration
    page.

    Write operations (altitude, pressure, asc, calibrate, reset) are
    protected by the admin password when one is configured.  The GET view
    (no write params) is always accessible so users can check calibration
    state without authentication.
    """
    global settings
    scd_available = (scd is not None)

    # Protect write operations with admin password if one is configured.
    _WRITE_PARAMS = {"reset", "calibrate", "altitude", "pressure", "asc", "update"}
    _has_write_op = params and any(k in params for k in _WRITE_PARAMS)
    _admin_pw = settings.get("admin_password", "")
    if _has_write_op and _admin_pw:
        _provided = params.get("pw", "")
        if _provided != _admin_pw:
            _esc_pw = _admin_pw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
    # If parameters were supplied, apply them.  We handle the "reset" action
    # first so that it overrides any other query parameters.  Resetting
    # restores defaults: ASC enabled, altitude/pressure disabled (0) and
    # clears the last calibration info.
    if params:
        if "reset" in params:
            # Reset calibration settings to defaults
            settings["asc_enabled"] = True
            settings["altitude"] = 0
            settings["ambient_pressure"] = 0
            settings["last_calibration_ts"] = 0
            settings["last_calibration_ref"] = 0
            # Apply defaults to the sensor
            if scd_available:
                _safe_setattr(scd, "self_calibration_enabled", True)
                _safe_setattr(scd, "altitude", 0)
                _scd_set_ambient_pressure(scd, 0)
            else:
                show_status("Sensor unavailable")
            save_settings()
            show_status("Calibration reset to defaults")
        else:
            # Preserve the ASC flag if it was previously True and not provided in the query
            if "asc" not in params and settings.get("asc_enabled", True):
                params["asc"] = "on"
            # Update altitude, clamping to allowed range.  A value of 0 disables compensation.
            if "altitude" in params and params["altitude"]:
                alt_val = _as_int(params["altitude"])
                if alt_val is not None:
                    if alt_val != 0:
                        alt_val = _clamp_int(alt_val, ALTITUDE_MIN, ALTITUDE_MAX, alt_val)
                    settings["altitude"] = alt_val
            # Update ambient pressure, clamping to allowed range.  A value of 0 disables compensation.
            if "pressure" in params and params["pressure"]:
                p_val = _as_int(params["pressure"])
                if p_val is not None:
                    if p_val != 0:
                        p_val = _clamp_int(p_val, PRESSURE_MIN_NONZERO, PRESSURE_MAX, p_val)
                    settings["ambient_pressure"] = p_val
            # Update ASC flag
            settings["asc_enabled"] = ("asc" in params)
            # Perform forced calibration if requested and a reference value was supplied
            if "calibrate" in params:
                try:
                    ref_val = int(params.get("ref", "0"))
                except Exception:
                    ref_val = None
                if ref_val:
                    if scd_available:
                        perform_force_calibration(ref_val)
                    else:
                        show_status("Sensor unavailable")
            # Apply calibration settings to the sensor immediately
            if scd_available:
                _safe_setattr(scd, "self_calibration_enabled", bool(settings.get("asc_enabled", True)))
                av = settings.get("altitude", 0)
                if av:
                    _safe_setattr(scd, "altitude", int(av))
                pv = settings.get("ambient_pressure", 0)
                if pv:
                    _scd_set_ambient_pressure(scd, pv)
            else:
                show_status("Sensor unavailable")
            # Persist changes
            save_settings()
            show_status("Calibration settings updated")
    # Render the calibration page (pass authenticated pw so the form can carry it forward)
    html = render_calibration_page(authed_pw=params.get("pw", "") if params else "")
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)

# ---------------------------------------------------------------------------
# ZIP package OTA helpers
# ---------------------------------------------------------------------------

def _zip_safe_path(name):
    """Return a cleaned, safe destination path for a ZIP entry, or None if unsafe."""
    name = name.replace("\\", "/")
    while name.startswith("/"):
        name = name[1:]
    # Skip directory entries, empty names, or any path containing ..
    if not name or name.endswith("/"):
        return None
    for part in name.split("/"):
        if part == ".." or part == ".":
            return None
    # Skip macOS resource-fork directories
    if name.startswith("__MACOSX/") or name.startswith("."):
        return None
    top = name.split("/")[0]
    # Only allow these top-level items
    if top in ("code.py", "boot.py"):
        return name if "/" not in name else None
    if top in ("lib", "assets"):
        return name
    return None


def _zip_ensure_dir(path):
    """Create all parent directories for path if they don't already exist."""
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
    """
    Parse a ZIP file's Central Directory and return a list of entry dicts.
    Each entry: {name, local_off, comp_size, uncomp_size, method}
    Returns (entries_list, None) on success, (None, error_str) on failure.
    Only STORED (method=0) and DEFLATE (method=8) are reported; others are skipped.
    """
    import struct as _s
    try:
        with open(zip_path, "rb") as f:
            # Determine file size
            f.seek(0, 2)
            file_size = f.tell()
            if file_size < 22:
                return None, "File too small to be a ZIP"

            # Scan backwards for the End-of-Central-Directory record (PK\x05\x06).
            # It is within the last 65536+22 bytes of the file.
            scan_size = min(file_size, 65558)
            f.seek(file_size - scan_size)
            tail = f.read(scan_size)
            eocd_rel = tail.rfind(b"PK\x05\x06")
            if eocd_rel < 0:
                return None, "Not a valid ZIP (no EOCD signature)"
            eocd = tail[eocd_rel : eocd_rel + 22]
            if len(eocd) < 22:
                return None, "Truncated EOCD record"

            # EOCD layout: sig(I) diskNo(H) cdStartDisk(H) entriesDisk(H)
            #              totalEntries(H) cdSize(I) cdOffset(I) commentLen(H)
            (sig, _, _, _, total_entries, cd_size, cd_offset, _) = \
                _s.unpack_from("<IHHHHIIH", eocd)
            if sig != 0x06054b50:
                return None, "Bad EOCD signature"
            if cd_size > 65536:
                return None, "Central Directory too large (>64 KB); ZIP64 not supported"

            # Read the Central Directory
            f.seek(cd_offset)
            cd_data = f.read(cd_size)
            if len(cd_data) < cd_size:
                return None, "Truncated central directory"

        # Parse Central Directory entries (all from cd_data in RAM)
        # CD entry fixed part (46 bytes):
        # sig(I) verMade(H) verNeeded(H) flags(H) method(H) modTime(H) modDate(H)
        # crc32(I) compSize(I) uncompSize(I) nameLen(H) extraLen(H) commentLen(H)
        # diskStart(H) intAttr(H) extAttr(I) localOff(I)
        CD_FMT = "<IHHHHHHIIIHHHHHII"
        CD_SZ  = 46
        entries = []
        pos = 0
        for _ in range(total_entries):
            if pos + CD_SZ > len(cd_data):
                break
            fields = _s.unpack_from(CD_FMT, cd_data, pos)
            (sig, _, _, _, method, _, _, _, comp_size, uncomp_size,
             name_len, extra_len, comment_len, _, _, _, local_off) = fields
            if sig != 0x02014b50:
                break
            raw_name = cd_data[pos + CD_SZ : pos + CD_SZ + name_len]
            try:
                name = raw_name.decode("utf-8")
            except Exception:
                name = raw_name.decode("latin-1")
            pos += CD_SZ + name_len + extra_len + comment_len
            # Skip directories and unsupported compression
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
    """
    Extract one entry from the ZIP at zip_path to dest_path.
    Supports STORED (method=0) and DEFLATE (method=8).
    Returns (ok: bool, message: str).
    """
    import struct as _s
    import gc as _gc
    method   = entry["method"]
    local_off  = entry["local_off"]
    comp_size  = entry["comp_size"]
    uncomp_size = entry["uncomp_size"]

    # Local file header layout (30 bytes fixed):
    # sig(I) verNeeded(H) flags(H) method(H) modTime(H) modDate(H)
    # crc32(I) compSize(I) uncompSize(I) nameLen(H) extraLen(H)
    LFH_FMT = "<IHHHHHIIIHH"
    LFH_SZ  = 30

    try:
        with open(zip_path, "rb") as zf:
            zf.seek(local_off)
            lhdr = zf.read(LFH_SZ)
            if len(lhdr) < LFH_SZ:
                return False, "Truncated local file header"
            (sig, _, _, _, _, _, _, _, _, _, name_len, extra_len) = \
                _s.unpack_from(LFH_FMT, lhdr)
            if sig != 0x04034b50:
                return False, "Bad local file header signature"
            # Seek past variable-length name and extra fields to reach data
            data_start = local_off + LFH_SZ + name_len + extra_len
            zf.seek(data_start)

            _zip_ensure_dir(dest_path)

            if method == 0:  # STORED — stream directly chunk by chunk
                written = 0
                with open(dest_path, "wb") as df:
                    while written < uncomp_size:
                        chunk = zf.read(min(512, uncomp_size - written))
                        if not chunk:
                            return False, "Premature end of STORED data at byte %d" % written
                        df.write(chunk)
                        written += len(chunk)
                        try:
                            if _wd is not None:
                                _wd.feed()
                        except Exception:
                            pass

            elif method == 8:  # DEFLATE — read compressed blob, decompress in RAM
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
    """
    Install a ZIP update package.  Called after the ZIP has been fully written
    to zip_path on disk.  Extracts allowed paths, validates code.py, does an
    atomic rename for code.py, then reboots.
    """
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
        _send_ota_result(conn, False, "ZIP is empty — no files found.")
        return

    # Validate paths; separate code.py from everything else
    safe  = []   # [(dest_path, entry), ...]
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
            "Allowed top-level names: code.py, boot.py, lib/, assets/. "
            "Found: " + ", ".join(e["name"] for e in entries[:8]))
        return

    # Extend watchdog for the duration of all extractions
    try:
        if _wd is not None:
            _wd.timeout = 90
    except Exception:
        pass

    # If code.py is in the package, extract it to a temp path and validate
    # before touching anything else on disk.
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
        # Sanity-check: must look like Python source
        try:
            with open(code_tmp, "rb") as _f:
                _head = _f.read(64).lstrip(b"\xef\xbb\xbf")
            _valid = (_head.startswith(b"#") or _head.startswith(b"import ") or
                      _head.startswith(b"from ")  or _head.startswith(b"\n#") or
                      _head.startswith(b"\r\n#"))
            if not _valid:
                try: _oz.remove(zip_path)
                except Exception: pass
                try: _oz.remove(code_tmp)
                except Exception: pass
                _send_ota_result(conn, False,
                    "code.py in ZIP does not look like Python source "
                    "(first bytes: %r). Aborting — nothing was changed." % _head[:16])
                return
        except Exception as ce:
            _send_ota_result(conn, False, "Cannot verify code.py: " + str(ce))
            return

    # Extract everything except code.py (already done to tmp)
    installed = []
    errors    = []
    for dest, e in safe:
        if dest == "code.py":
            continue  # handled separately
        ok, msg = _extract_zip_entry_to_file(zip_path, e, dest)
        if ok:
            installed.append(dest)
        else:
            errors.append("%s: %s" % (dest, msg))
        _gcz.collect()

    # Atomic rename of code.py into place (last, so other files are already on disk)
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

    # Remove the ZIP from disk
    try: _oz.remove(zip_path)
    except Exception: pass

    # Build summary
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
    """OTA firmware update page.
    GET  - shows the update form (URL download + file upload).
    POST with firmware_url param - downloads from URL and installs.
    POST with ?upload=1 query param and raw binary body - streams file directly to disk.
    """
    global settings

    # Require admin password if set (checked for GET and POST form submissions;
    # file uploads pass pw as a query param since the body is raw binary).
    admin_pw = settings.get("admin_password", "")
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
        # Snapshot settings to backup file before any OTA write so they can be
        # restored automatically on next boot if the filesystem gets corrupted.
        try:
            with open(SETTINGS_FILE, "rb") as _sf:
                _sdata = _sf.read()
            with open(SETTINGS_FILE + ".bak", "wb") as _bf:
                _bf.write(_sdata)
        except Exception:
            pass
        # Free space before writing the upload so the filesystem doesn't fill up
        # mid-stream. Remove any leftover temp from a previous failed attempt and
        # the backup copy from the last successful OTA (both are ~238 KB each).
        try:
            import os as _pre_os
            try: _pre_os.remove("/update.tmp")
            except Exception: pass
            try: _pre_os.remove("/code.py.bak")
            except Exception: pass
        except Exception:
            pass
        tmp_path = "/update.tmp"
        ok, msg = _stream_request_body_to_file(conn, raw_headers, tmp_path)
        if not ok:
            _send_ota_result(conn, False, "Upload failed: " + msg)
            try:
                import os as _os2
                _os2.remove(tmp_path)
            except Exception:
                pass
            return

        # Detect file type by magic bytes.
        # PK\x03\x04 (0x504B0304) → ZIP package   anything else → Python source
        try:
            with open(tmp_path, "rb") as _f:
                _magic = _f.read(4)
        except Exception as _me:
            _send_ota_result(conn, False, "Cannot read uploaded file: " + str(_me))
            return

        if _magic == b"PK\x03\x04":
            # --- ZIP package update ---
            import os as _oz_mv
            zip_path = "/update.zip"
            try:
                _oz_mv.rename(tmp_path, zip_path)
            except Exception:
                zip_path = tmp_path  # use in-place if rename fails
            _process_zip_update(conn, zip_path)
            return

        # --- Single Python file update (original code.py-only path) ---
        _head_str = _magic + b""  # first 4 bytes already read
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
                "Python source (first bytes: %r). Aborting — nothing changed." % _head_str[:16])
            return
        # Rename temp → code.py atomically
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

    # --- URL DOWNLOAD: POST with firmware_url in body ---
    if method == b"POST" and "firmware_url" in params:
        fw_url = params.get("firmware_url", "").strip()
        if not fw_url:
            _send_ota_result(conn, False, "No URL provided.")
            return
        if wifi_mode != WIFI_MODE_STA or wifi is None or not wifi.radio.connected:
            _send_ota_result(conn, False, "Must be in STA (WiFi) mode to download firmware.")
            return
        # Snapshot settings before any write so they survive a failed OTA.
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
            # Extend watchdog timeout before writing — same reason as file upload.
            try:
                if _wd is not None:
                    _wd.timeout = 90
            except Exception:
                pass
            # Stream response to disk to avoid RAM overflow on large files.
            with open(tmp_path, "wb") as f:
                try:
                    # iter_content available in adafruit_requests >= 4.x
                    for chunk in response.iter_content(chunk_size=512):
                        if chunk:
                            f.write(chunk)
                            try:
                                if _wd is not None:
                                    _wd.feed()
                            except Exception:
                                pass
                except AttributeError:
                    # Fallback: load into RAM (smaller firmwares only)
                    f.write(response.content)
            response.close()
            # Sanity-check the downloaded file before installing it.
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
    pw_qs = ("&pw=" + pw_val) if pw_val else ""
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

<h2>Option 1 — Upload from your computer</h2>
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
<code>code.py</code> &nbsp; <code>boot.py</code> &nbsp; <code>lib/</code> &nbsp; <code>assets/</code><br><br>
macOS &amp; Linux:
<pre>zip -r knowco2-update.zip code.py boot.py lib/ assets/</pre>
Windows (PowerShell — requires 7-Zip or similar for best results):
<pre>7z a knowco2-update.zip code.py boot.py lib\\ assets\\</pre>
Any file outside those four paths is safely ignored.<br>
<strong>Your settings (Wi-Fi, thresholds, etc.) are never touched by an update.</strong>
</div>

<hr>
<h2>Option 2 — Download from a URL (STA mode only)</h2>
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
    # If an admin password is configured, require that the caller supply a matching
    # "pw" query parameter.  If no valid password is supplied, present a simple
    # login form instead of the settings page.  The password can be set or
    # cleared via the "admin_pw" field on the settings form.  The /status and
    # /data routes remain unauthenticated.
    try:
        admin_pw = settings.get("admin_password", "")
    except Exception:
        admin_pw = ""
    if admin_pw:
        provided_pw = params.get("pw")  # password supplied via query param
        # If the caller did not supply a password or it does not match, show a login page.
        if not provided_pw or provided_pw != admin_pw:
            # Render a simple login page with a password form.  When the form is
            # submitted, the password will appear as the "pw" query parameter on
            # the URL.  This avoids asking the user to hand-edit the URL.  The
            # login page shares the dark styling of the main UI.
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
    <h1>Know CO₂</h1>
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

    # If any parameters were supplied (besides the login password), apply them to update settings.
    # Remove the login password parameter so it is not interpreted as a setting.
    settings_params = None
    if params:
        # Create a copy of params without the "pw" key.
        settings_params = {}
        for k, v in params.items():
            if k != "pw":
                settings_params[k] = v
    if settings_params and len(settings_params) > 0:
        # Preserve boolean settings (alerts_enabled and cloud_enabled) when not provided.  When the
        # user submits the settings form without explicitly toggling these checkboxes, the
        # corresponding parameters will be absent from the query string.  In that case, we want
        # to retain the existing True values rather than resetting them to False.  We do this
        # by injecting the current value back into the parameter map so update_settings_from_params
        # will keep the setting enabled.  This still allows the user to disable the feature by
        # unchecking the box (which removes the key entirely when the setting is currently True).
        if "cloud_enabled" not in settings_params and settings.get("cloud_enabled", False):
            settings_params["cloud_enabled"] = "on"
        if "alerts" not in settings_params and settings.get("alerts_enabled", False):
            settings_params["alerts"] = "on"

        ap_changed = update_settings_from_params(settings_params)
        update_visibility()
        refresh_text()

        # If AP creds changed while in AP mode, restart AP
        if ap_changed and wifi_mode == WIFI_MODE_AP:
            switch_to_ap(force_restart=True)

        if screen == SCREEN_APINFO:
            if wifi_mode == WIFI_MODE_AP:
                make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached or "192.168.4.1")
            refresh_apinfo_screen()

        show_status("AP regenerated" if "regen_ap" in settings_params else "Web settings updated")

    html = render_settings_page()
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)

def start_http_server():
    """Start (or restart) the tiny HTTP server in either AP or STA mode.
    Returns True on success, False on failure.
    """
    global http_server_sock, socket_pool

    if wifi is None or socketpool is None:
        print("HTTP server: wifi/socketpool unavailable")
        return False

    # Close old socket if present
    try:
        if http_server_sock is not None:
            try:
                http_server_sock.close()
            except Exception:
                pass
            http_server_sock = None
    except Exception:
        pass

    try:
        socket_pool = socketpool.SocketPool(wifi.radio)
        srv = socket_pool.socket(socket_pool.AF_INET, socket_pool.SOCK_STREAM)

        # Reuse address if supported
        try:
            srv.setsockopt(socket_pool.SOL_SOCKET, socket_pool.SO_REUSEADDR, 1)
        except Exception:
            pass

        # Prefer binding to the AP IP when in AP mode (some stacks behave better than 0.0.0.0)
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
            # Fallback to 0.0.0.0 if binding to AP IP failed
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

        http_server_sock = srv
        print("HTTP server listening on %s:80" % bind_ip)
        return True

    except Exception as e:
        print("HTTP server start error:", e)
        try:
            http_server_sock = None
        except Exception:
            pass
        return False

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
            if wifi_mode == WIFI_MODE_AP:
                # In AP mode return a redirect so iOS/Android shows the
                # "Sign in to Network" captive-portal popup automatically.
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

        # For POST requests, read the body and merge into params (body params override query params).
        # OTA file uploads stream the body directly to disk, so skip generic body parsing
        # for those requests — reading even 8192 bytes here would consume the start of the
        # firmware payload before _stream_request_body_to_file can see it.
        _is_ota_upload = (route == "/update" and "upload" in params)
        if method == b"POST" and not _is_ota_upload:
            post_body = _read_request_body(conn, data)
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
            # Default to the settings page for all other routes (including "/").
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
    global wifi_mode, ip_str_cached, _cloud_session, _cloud_ctx

    if wifi is None or socketpool is None:
        show_status("WiFi not available")
        return False

    # Invalidate cloud session so a fresh one is created for the new network context.
    _cloud_session = None
    _cloud_ctx = None

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

        ok_http = start_http_server()

        if not ok_http:

            show_status('HTTP: error')
        if screen == SCREEN_APINFO:
            make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached)
            refresh_apinfo_screen()
        return True

    except Exception as e:
        print("AP start error:", e)
        show_status("AP error")
        return False

def ensure_sta_connected():
    # Rate-limited reconnect: wifi.radio.connect() can block 10-30 s, so we only
    # attempt it after STA_RECONNECT_COOLDOWN_S seconds have elapsed.
    global last_sta_reconnect_attempt
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

    # Cooldown check: don't attempt reconnect too frequently.
    now_mono = time.monotonic()
    if (now_mono - last_sta_reconnect_attempt) < STA_RECONNECT_COOLDOWN_S:
        return False

    last_sta_reconnect_attempt = now_mono

    # Feed the hardware watchdog before a potentially long connect() call so
    # the device is not reset mid-connect by a stale watchdog counter.
    if _wd is not None:
        try:
            _wd.feed()
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
    global wifi_mode, ip_str_cached, _cloud_session, _cloud_ctx, last_sta_reconnect_attempt

    if wifi is None or socketpool is None:
        show_status("WiFi not available")
        return False

    # Clear cloud session and reset cooldown so the first post-switch reconnect is immediate.
    _cloud_session = None
    _cloud_ctx = None
    last_sta_reconnect_attempt = 0.0

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

    ok_http = start_http_server()

    if not ok_http:

        show_status('HTTP: error')
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
    return _clamp_int(backoff, 15, CLOUD_MAX_BACKOFF, backoff)

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
    # Feed the watchdog immediately before the POST; adafruit_requests can
    # block up to `timeout` seconds and would otherwise stale the counter.
    if _wd is not None:
        try:
            _wd.feed()
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
        # Also feed the watchdog on exit so a slow response never leaves the
        # counter stale for the rest of the main loop.
        if _wd is not None:
            try:
                _wd.feed()
            except Exception:
                pass

# ======================================================================
#  MQTT PUBLISHING
#  Supports generic MQTT broker (e.g. Home Assistant) and Adafruit IO.
#  Uses connect-publish-disconnect per interval to keep memory clean.
# ======================================================================

last_mqtt_send = 0.0
last_aio_send = 0.0

def _mqtt_publish_one(broker, port, user, password, topics_payloads, use_ssl=False):
    """Connect to an MQTT broker, publish a list of (topic, payload, retain) tuples, disconnect."""
    if not _HAS_MQTT or MQTT is None:
        return False
    if wifi is None or not wifi.radio.connected:
        return False
    # Feed the watchdog before any blocking network call, collect garbage to
    # prevent socket exhaustion from fragmented prior allocations, and reuse
    # the global SocketPool instead of creating a new one each time (the
    # ESP32-S3 has a fixed small socket limit; a new pool per call burns them).
    gc.collect()
    if _wd is not None:
        try:
            _wd.feed()
        except Exception:
            pass
    pool = socket_pool  # reuse global SocketPool
    if pool is None:
        if socketpool is not None:
            pool = socketpool.SocketPool(wifi.radio)
        else:
            return False
    try:
        mqtt_client = MQTT.MQTT(
            broker=broker,
            port=int(port),
            username=user or None,
            password=password or None,
            socket_pool=pool,
            ssl_context=None,
            connect_retries=1,
            socket_timeout=5,
            keep_alive=15,
        )
        mqtt_client.connect()
        for topic, payload, retain in topics_payloads:
            try:
                mqtt_client.publish(topic, payload, retain=retain)
            except Exception as e:
                print("MQTT publish error:", topic, e)
        mqtt_client.disconnect()
        gc.collect()  # free MQTT buffers promptly
        return True
    except Exception as e:
        gc.collect()
        print("MQTT error:", e)
        return False


def publish_to_mqtt():
    """Publish current CO2/temp/RH to the configured MQTT broker."""
    global last_mqtt_send
    broker = settings.get("mqtt_broker", "").strip()
    if not broker:
        return False
    port = settings.get("mqtt_port", 1883)
    user = settings.get("mqtt_user", "")
    password = settings.get("mqtt_pass", "")
    prefix = (settings.get("mqtt_topic_prefix", "knowco2") or "knowco2").strip()
    topics = []
    if last_co2 is not None:
        topics.append(("%s/co2" % prefix, str(int(last_co2)), False))
    if last_temp_c is not None:
        topics.append(("%s/temp_c" % prefix, "%.2f" % last_temp_c, False))
    if last_rh is not None:
        topics.append(("%s/rh" % prefix, "%.2f" % last_rh, False))
    if not topics:
        return False
    publish_mqtt_discovery()
    ok = _mqtt_publish_one(broker, port, user, password, topics)
    if ok:
        log("mqtt", "MQTT published to", broker, min_interval=30.0)
    else:
        log("mqtt_err", "MQTT publish failed to", broker, min_interval=30.0)


_mqtt_discovery_sent = False

def publish_mqtt_discovery():
    """Publish Home Assistant MQTT discovery config (once per boot).
    This allows Home Assistant to auto-discover KnowCO2 sensors without
    any manual configuration.yaml editing by the user."""
    global _mqtt_discovery_sent
    if _mqtt_discovery_sent:
        return
    broker = settings.get("mqtt_broker", "").strip()
    if not broker:
        return
    port   = settings.get("mqtt_port", 1883)
    user   = settings.get("mqtt_user", "")
    pw     = settings.get("mqtt_pass", "")
    prefix = (settings.get("mqtt_topic_prefix", "knowco2") or "knowco2").strip()
    uid    = (hwid_hex or settings.get("device_id", "co2-node-1") or "co2-node-1").lower()
    device = {
        "identifiers": ["knowco2_%s" % uid],
        "name": "KnowCO2",
        "manufacturer": "KNOWCO2 LLC",
        "model": "KnowCO2 Model A",
        "sw_version": FIRMWARE_VERSION,
    }
    sensors = [
        ("co2",    "CO2",         "ppm", "carbon_dioxide", "%s/co2"    % prefix),
        ("temp_c", "Temperature", "°C",  "temperature",    "%s/temp_c" % prefix),
        ("rh",     "Humidity",    "%",   "humidity",       "%s/rh"     % prefix),
    ]
    topics = []
    for key, name, unit, dc, state_topic in sensors:
        cfg_topic = "homeassistant/sensor/knowco2_%s/%s/config" % (uid, key)
        payload = json.dumps({
            "name": "KnowCO2 %s" % name,
            "unique_id": "knowco2_%s_%s" % (uid, key),
            "state_topic": state_topic,
            "unit_of_measurement": unit,
            "device_class": dc,
            "device": device,
        })
        topics.append((cfg_topic, payload, True))
    ok = _mqtt_publish_one(broker, port, user, pw, topics)
    if ok:
        _mqtt_discovery_sent = True
        log("mqtt", "HA MQTT discovery published", min_interval=60.0)
    else:
        log("mqtt_err", "HA MQTT discovery failed", min_interval=60.0)


def publish_to_adafruit_io():
    """Publish current readings to Adafruit IO via MQTT."""
    global last_aio_send
    aio_user = settings.get("aio_username", "").strip()
    aio_key = settings.get("aio_key", "").strip()
    if not aio_user or not aio_key:
        return False
    group = (settings.get("aio_group_key", "knowco2") or "knowco2").strip()
    # Adafruit IO MQTT topic format: <username>/feeds/<group>.<feed>
    topics = []
    if last_co2 is not None:
        topics.append(("%s/feeds/%s.co2" % (aio_user, group), str(int(last_co2)), False))
    if last_temp_c is not None:
        topics.append(("%s/feeds/%s.temperature" % (aio_user, group), "%.2f" % last_temp_c, False))
    if last_rh is not None:
        topics.append(("%s/feeds/%s.humidity" % (aio_user, group), "%.2f" % last_rh, False))
    if not topics:
        return False
    ok = _mqtt_publish_one("io.adafruit.com", 1883, aio_user, aio_key, topics)
    if ok:
        log("aio", "Adafruit IO published", min_interval=30.0)
    else:
        log("aio_err", "Adafruit IO publish failed", min_interval=30.0)
    return ok

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


def _poll_buttons_during_block():
    """Poll button B during any blocking operation (graph redraw, MQTT, cloud upload,
    HTTP response) so a press is never silently lost.

    Sets _btn_b_pending = True on a rising edge.  The main loop processes the
    pending flag just as it would a live button press.  Call this every ~10 loop
    iterations inside any long inner loop.
    """
    global _btn_b_pending, prev_b
    try:
        b = read_b()
        if b and not prev_b:
            _btn_b_pending = True
        prev_b = b
    except Exception:
        pass


# ======================================================================
#  ENERGY (LOW POWER) MODE  +  BATTERY BOOT CHECK
# ======================================================================

def apply_energy_mode(active):
    """Switch into (active=True) or out of (active=False) Low Power mode.

    LP mode changes:
      • SCD41 → 30 s low-power periodic measurement (sensor draws ~0.4 mA avg)
        SCD40/SCD30 → stays at 5 s but skips intermediate loop iterations
      • Display brightness drops to ENERGY_LP_BRIGHTNESS (20 %)
      • Main-loop sleep doubles to ENERGY_LP_SLEEP_S (50 ms)
      • Cloud / MQTT / AIO upload intervals multiply by their respective multipliers
      • LP badge appears top-left on all screens
    All Wi-Fi connectivity, HTTP server, calibration, and OTA remain fully active.
    """
    global energy_mode, _scd_period_effective, _save_deferred_ts

    energy_mode = active
    settings["energy_mode"] = active
    _save_deferred_ts = time.monotonic() + 2.0  # persist shortly after

    # ── Sensor mode ────────────────────────────────────────────────────────
    if scd is not None:
        try:
            # Stop whatever mode is currently running
            if hasattr(scd, "stop_periodic_measurement"):
                scd.stop_periodic_measurement()
            elif hasattr(scd, "stop_continuous_measurements"):
                scd.stop_continuous_measurements()
            time.sleep(0.3)
        except Exception:
            pass
        try:
            lp_capable = (sensor_model_str == "SCD41" and
                          hasattr(scd, "start_low_power_periodic_measurement"))
            if active and lp_capable:
                scd.start_low_power_periodic_measurement()
                _scd_period_effective = 30.0
                print("SCD41: low-power periodic measurement (30 s)")
            else:
                scd.start_periodic_measurement()
                _scd_period_effective = 5.0
                if active:
                    print("SCD40/SCD30 has no LP mode; normal 5 s measurement")
        except Exception as e:
            print("Energy mode sensor switch failed:", e)
            _scd_period_effective = 5.0

    # ── Display brightness ─────────────────────────────────────────────────
    try:
        board.DISPLAY.brightness = ENERGY_LP_BRIGHTNESS if active else 1.0
    except Exception:
        pass

    # ── LP badge ───────────────────────────────────────────────────────────
    try:
        lp_badge_label.hidden = not active
    except Exception:
        pass

    # Reset staleness timestamp so the watchdog does not fire immediately
    # after the mode switch while the sensor is resuming measurements.
    try:
        global last_scd_sample_ts
        last_scd_sample_ts = time.monotonic()
    except Exception:
        pass

    show_status("LP Mode: ON — hold A to exit" if active else "LP Mode: OFF")
    print("Energy mode:", "ON" if active else "OFF",
          "| SCD period:", _scd_period_effective, "s")


def check_battery_boot():
    """Check battery voltage at boot and warn if critically low.

    WHY THE DEVICE MAY NOT TURN ON AFTER FULL DISCHARGE
    ─────────────────────────────────────────────────────
    When a LiPo cell fully discharges, the on-cell protection circuit (DW01 /
    FS8205 or equivalent) disconnects the cell to prevent damage.  The Feather's
    MCP73831 charger IC sees 0 V on the battery rail and may not start the
    charge cycle until the rail rises above ~2.9 V.

    Recovery steps for users:
      1. Connect USB-C and wait 60–90 seconds WITHOUT pressing anything.
         The charger will pre-condition the cell at 10 % current (~50 mA) until
         it rises above the 3 V threshold, then switch to full CC/CV charging.
      2. If the screen stays blank after 60 s, press the RST button once.
      3. The device will boot normally once the cell reaches ~3.3 V.
      4. Do NOT repeatedly press RST — it interrupts the pre-conditioning phase.

    From a firmware perspective we cannot force the protection IC to re-engage,
    but we can avoid making things worse:
      • We do not attempt Wi-Fi or cloud uploads until the battery is stable.
      • We show a clear "CHARGING" banner so the user knows what is happening.
    """
    if fuel_gauge is None:
        return
    try:
        v, p = read_battery()
        if v is None:
            return
        print("Battery at boot: %.2f V  %s%%" % (v, str(p) if p is not None else "?"))
        if v < BATT_BOOT_WARN_V:
            # Show a clean, non-intrusive charging notice using the small
            # status label rather than hijacking the large co2_label.
            # This avoids the jarring orange flash during the boot sequence.
            try:
                status_label.text = "Battery low — keep USB connected"
                th_label.text = "%.2fV  Charging..." % v
                th_label.hidden = False
                display.root_group = main_group
                time.sleep(4)
                status_label.text = ""
                th_label.text = ""
            except Exception:
                pass
    except Exception as e:
        print("Battery boot check error:", e)

# ======================================================================
#  BOOT
# ======================================================================

init_ids()
init_pair_code()
init_mdns_hostname()

# Set the DHCP client hostname so the device appears as "knowco2-xxxx" in
# router device lists and network scanners (not the CircuitPython default).
# Must be done before any WiFi connection attempt.
if wifi is not None and mdns_hostname:
    try:
        wifi.radio.hostname = mdns_hostname
    except Exception:
        pass

load_settings()
ensure_ap_credentials()
apply_settings()
init_fuel_gauge()
check_battery_boot()

update_visibility()

# Prefer STA if configured, otherwise AP. If STA fails at startup (e.g. router not
# ready yet), fall back to AP and schedule background retries from the main loop.
if (settings.get("sta_ssid") or "").strip() and (settings.get("sta_password") or "").strip():
    if not switch_to_sta():
        _sta_fallback = True  # main loop will retry periodically
        switch_to_ap()
else:
    switch_to_ap()

# Restore Low Power mode if it was active before the last reboot.
if settings.get("energy_mode", False):
    apply_energy_mode(True)

# ======================================================================
#  MAIN LOOP
# ======================================================================

last_sensor = 0.0
last_apinfo_refresh = 0.0
last_batt_refresh = 0.0
last_wifi_ind_refresh = 0.0
cached_vbat = None
cached_pct = None
last_dim_check = 0.0
# Deferred settings save: set to (now + delay) when a button triggers a settings
# change that doesn't need an instant flash write.  Avoids blocking the main loop
# on the button press itself — the actual save happens once the deadline passes.
_save_deferred_ts = 0.0


# ======================================================================
#  HARD WATCHDOG (consumer-safety)
#  If the main loop stalls (e.g., I2C hang), reset the MCU.
#  Enabled right before the main loop to avoid resets during boot sleeps.
# ======================================================================
_wd = None
try:
    from watchdog import WatchDogMode
    if microcontroller is not None:
        _wd = microcontroller.watchdog
        # 20 s timeout: WiFi connect() can block up to ~15 s; watchdog is also
        # fed explicitly before other long-running operations.
        _wd.timeout = 20
        _wd.mode = WatchDogMode.RESET
except Exception as e:
    _wd = None
    print("watchdog unavailable:", e)


while True:
    # Feed hardware watchdog each loop so any hard stall triggers a reset.
    if _wd is not None:
        try:
            _wd.feed()
        except Exception:
            pass
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

    # D0 (A) — short press toggles °C/°F  |  hold 2 s → toggle LP mode.
    # This mirrors button C (D2) which also uses short/long-press patterns.
    if a_now and (not prev_a):
        _btn_a_hold_start = now
        _btn_a_hold_fired = False

    if a_now and (_btn_a_hold_start is not None) and (not _btn_a_hold_fired):
        if (now - _btn_a_hold_start) >= LP_A_HOLD_SECONDS:
            _btn_a_hold_fired = True
            apply_energy_mode(not energy_mode)
            settings["energy_mode"] = energy_mode

    if (not a_now) and prev_a:
        # Button A released — process as short press if hold did not fire
        if (_btn_a_hold_start is not None) and (not _btn_a_hold_fired):
            if screen == SCREEN_REGULATORY:
                # Any short press on regulatory screen returns to info screen.
                screen = SCREEN_APINFO
                update_visibility()
                refresh_apinfo_screen()
            elif screen == SCREEN_MAIN:
                temp_mode = "C" if temp_mode == "F" else "F"
                settings["temp_mode"] = temp_mode
                _save_deferred_ts = now + 1.5
                refresh_text()
                show_status("Temp: " + temp_mode)
        _btn_a_hold_start = None
        _btn_a_hold_fired = False

    # D1 (B) — track hold start on rising edge (for regulatory screen on SCREEN_APINFO)
    if b_now and (not prev_b):
        _btn_b_hold_start = now
        _btn_b_hold_fired = False

    # D1 (B) — fire hold action: open regulatory screen when held on SCREEN_APINFO
    if b_now and (_btn_b_hold_start is not None) and (not _btn_b_hold_fired):
        if (now - _btn_b_hold_start) >= B_HOLD_SECONDS:
            if screen == SCREEN_APINFO:
                _btn_b_hold_fired = True
                screen = SCREEN_REGULATORY
                update_visibility()

    # D1 (B) — on release: handle short press or return from regulatory screen.
    # Also handles presses captured during blocking ops via _btn_b_pending.
    if (not b_now) and (prev_b or _btn_b_pending):
        _btn_b_pending = False
        if screen == SCREEN_REGULATORY:
            # Any release on the regulatory screen returns to info screen.
            screen = SCREEN_APINFO
            update_visibility()
            refresh_apinfo_screen()
        elif not _btn_b_hold_fired:
            # Existing short-press behaviour: APINFO → MAIN, or cycle display mode.
            if screen == SCREEN_APINFO:
                screen = SCREEN_MAIN
                update_visibility()
            if screen == SCREEN_MAIN:
                display_mode = (display_mode + 1) % 3
                settings["display_mode"] = display_mode
                _save_deferred_ts = now + 1.5  # persist to flash shortly after, not during press
                update_visibility()
                # If the user switched into graph mode, schedule a redraw rather than doing it immediately.
                if display_mode == 2:
                    graph_refresh_needed = True
                refresh_text()
                show_status("Mode: " + mode_name())
        _btn_b_hold_start = None
        _btn_b_hold_fired = False

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
                _sta_fallback = False  # user explicitly chose AP; don't auto-switch back
                switch_to_ap(force_restart=True)
            else:
                show_status("Switching to STA...")
                if not switch_to_sta():
                    show_status("STA failed; AP")
                    switch_to_ap(force_restart=True)

    if (not c_now) and prev_c:
        # released
        if d2_hold_start is not None and (not d2_hold_fired):
            # short press behaviour:
            #   - When on SCREEN_REGULATORY: return to info screen.
            #   - When on SCREEN_APINFO in AP mode: cycle QR page (0->1->0).
            #   - Otherwise: toggle between SCREEN_MAIN and SCREEN_APINFO.
            if screen == SCREEN_REGULATORY:
                screen = SCREEN_APINFO
                update_visibility()
                refresh_apinfo_screen()
            elif screen == SCREEN_APINFO and wifi_mode == WIFI_MODE_AP:
                _qr_page = 1 - _qr_page          # toggle 0<->1
                _last_wifi_payload = None         # force QR rebuild for new page
                make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached or "192.168.4.1")
            else:
                screen = SCREEN_APINFO if screen == SCREEN_MAIN else SCREEN_MAIN
                update_visibility()
                if screen == SCREEN_APINFO:
                    _qr_page = 0           # always start at page 0 (WiFi QR) when entering
                    _last_wifi_payload = None
                    if wifi_mode == WIFI_MODE_AP:
                        make_or_update_qrs(settings.get("ap_ssid", ""), settings.get("ap_password", ""), ip_str_cached or "192.168.4.1")
                    refresh_apinfo_screen()
                else:
                    refresh_text()
                    if display_mode == 2:
                        graph_refresh_needed = True
        d2_hold_start = None
        d2_hold_fired = False

    prev_a = a_now
    prev_b = b_now
    prev_c = c_now

    _wifi_ind_interval = 10.0 if energy_mode else 1.0
    if now - last_wifi_ind_refresh > _wifi_ind_interval:
        last_wifi_ind_refresh = now
        update_wifi_indicator()

    _batt_interval = 30.0 if energy_mode else 2.0
    if now - last_batt_refresh > _batt_interval:
        last_batt_refresh = now
        vv, pp = read_battery()
        if vv is not None:
            cached_vbat, cached_pct = vv, pp
        # Update low-battery warning banner
        try:
            _batt_low = (fuel_gauge is not None and
                         cached_pct is not None and
                         cached_pct < BATT_WARN_PCT)
            batt_warn_label.hidden = not (screen == SCREEN_MAIN and _batt_low)
            if _batt_low:
                batt_warn_label.text = "!! BATT %d%%" % int(cached_pct)
        except Exception:
            pass

    if screen == SCREEN_APINFO and (now - last_apinfo_refresh > (10.0 if energy_mode else 1.0)):
        last_apinfo_refresh = now
        refresh_apinfo_screen()

    # Poll the sensor less often and handle CRC failures gracefully
    if now - last_sensor > 1.0:
        last_sensor = now
        if scd is None:
            if scd_init_failed and (not sensor_warned):
                show_status("Sensor unavailable")
                sensor_warned = True
        else:
            try:
                # SCD-4x uses data_ready; SCD-30 uses data_available.
                _data_ready = (scd.data_available if hasattr(scd, "data_available")
                               else scd.data_ready)
                if _data_ready:
                    co2 = scd.CO2
                    temp_c = scd.temperature
                    rh = scd.relative_humidity

                    # reset failure counter on successful read
                    scd_crc_failures = 0
                    scd_recoveries = 0

                    # Compute instantaneous rate of change (ppm per second) using the previous
                    # CO₂ value.  If no previous value exists, leave it undefined.
                    prev_co2 = last_co2
                    if prev_co2 is not None:
                        # Rate is difference divided by sample period (positive for rising values,
                        # negative for falling).  Keep as floating point for display.
                        rate_of_change = (co2 - prev_co2) / _scd_period_effective
                    else:
                        rate_of_change = None

                    last_co2_prev = last_co2
                    last_co2 = co2
                    last_temp_c = temp_c
                    last_rh = rh

                    # Update the timestamp of the last successful sample.
                    # This timestamp is used by the staleness watchdog below to
                    # detect when the sensor stops providing new data.  We use
                    # the `now` value from the main loop (seconds since boot)
                    # rather than calling time.monotonic() again to avoid
                    # drift.
                    last_scd_sample_ts = now

                    if screen == SCREEN_MAIN:
                        refresh_text()
                        if last_co2 is not None:
                            apply_alert_colors(last_co2)
                            # Only trigger an alert message once when CO₂ rises above
                            # the alert threshold.  This avoids continually resetting
                            # the status timeout on every sample when the gas level
                            # remains high.  Reset the alert flag when the value
                            # drops back below the threshold.
                            if alerts_enabled:
                                if last_co2 >= ALERT_THRESHOLD:
                                    if not alert_triggered:
                                        show_status("ALERT: %d ppm" % int(last_co2))
                                        alert_triggered = True
                                else:
                                    alert_triggered = False

                    co2_history.append(last_co2)
                    if len(co2_history) > MAX_POINTS:
                        co2_history[:] = co2_history[-MAX_POINTS:]

                    # If we're showing the graph, schedule a redraw to update it with the new sample.
                    if screen == SCREEN_MAIN and display_mode == 2:
                        graph_refresh_needed = True

            except RuntimeError as err:
                # increment failure count on CRC (data integrity) errors
                scd_crc_failures += 1
                log("scd_crc", "SCD read error:", err, "fails:", scd_crc_failures, min_interval=1.0)
                # if too many failures in a row, attempt to recover
                if scd_crc_failures >= SCD_MAX_FAILS_BEFORE_RESET:
                    scd_recover()

            except Exception as err:
                # log other unexpected errors but do not crash the loop
                log("scd_other", "SCD unexpected error:", err, min_interval=1.0)

            # If the sensor has not produced a new sample within the
            # SCD_SAMPLE_TIMEOUT window, the watchdog triggers a recovery.
            # This check runs after each sensor poll (whether successful or
            # failed) and ensures we recover from bus hangs or sensor
            # lock‑ups that leave data frozen but the UI still responsive.
            try:
                _effective_scd_timeout = max(SCD_SAMPLE_TIMEOUT,
                                             _scd_period_effective * 2.5)
                _scd_age = time.monotonic() - last_scd_sample_ts
                if _scd_age > _effective_scd_timeout:
                    show_status("SCD: timeout")
                    scd_recover()
                    # Reset the last sample timestamp so we don't immediately
                    # trigger another recovery if the sensor is still warming up.
                    last_scd_sample_ts = time.monotonic()
            except Exception:
                # Don't let watchdog errors crash the loop; they will be logged
                pass

    # Update sensor-frozen banner each loop so it appears/clears immediately.
    try:
        _scd_age_now = time.monotonic() - last_scd_sample_ts
        # In LP mode the sensor updates every 30 s; use 1.5× the effective
        # period to avoid a false "SENSOR ERR" banner between samples.
        _effective_frozen_warn = max(SENSOR_FROZEN_WARN_SEC,
                                     _scd_period_effective * 1.5)
        _frozen = _scd_age_now > _effective_frozen_warn
        if _frozen != sensor_frozen_shown:
            sensor_frozen_shown = _frozen
            sensor_frozen_label.hidden = not (screen == SCREEN_MAIN and sensor_frozen_shown)
    except Exception:
        pass

    # Last-resort hard MCU reset if the sensor has been frozen beyond SENSOR_HARD_RESET_SEC.
    try:
        _effective_hard_reset = max(SENSOR_HARD_RESET_SEC,
                                    _scd_period_effective * 4.0)
        if (time.monotonic() - last_scd_sample_ts) > _effective_hard_reset:
            show_status("SCD: hard reset")
            time.sleep(0.5)
            if microcontroller is not None:
                microcontroller.reset()
    except Exception:
        pass

    # NTP sync (STA only) — rate-limited so failed attempts don't stall the main loop.
    if wifi_mode == WIFI_MODE_STA:
        try:
            overdue = (not ntp_synced) or ntp_sync_pending or ((now - last_ntp_sync) > NTP_SYNC_INTERVAL)
            attempt_ok = (now - last_ntp_attempt) >= NTP_MIN_RETRY_S
            due = overdue and attempt_ok
        except Exception:
            due = False
        if due:
            last_ntp_attempt = now
            ntp_sync(force=False)

        # Cloud upload (periodic) - STA only
        if cloud_enabled and wifi_mode == WIFI_MODE_STA:
            interval = cloud_next_interval()
            # In LP mode (or critical battery) reduce upload rate further
            if energy_mode:
                interval = max(interval, cloud_interval_sec * ENERGY_LP_CLOUD_MULT)
            if cached_pct is not None and cached_pct < BATT_CRIT_PCT:
                interval = interval * 2  # critical battery: halve upload rate
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
                    # Do not display "Cloud: OK" as a status message.
                else:
                    cloud_failures += 1

        # MQTT publish (periodic) - STA only
        mqtt_enabled = settings.get("mqtt_enabled", False)
        if mqtt_enabled and settings.get("mqtt_broker", "").strip():
            mqtt_interval = max(15, int(settings.get("mqtt_interval_sec", 60) or 60))
            if energy_mode:
                mqtt_interval = mqtt_interval * ENERGY_LP_MQTT_MULT
            if now - last_mqtt_send > mqtt_interval:
                last_mqtt_send = now
                publish_to_mqtt()

        # Adafruit IO publish (periodic) - STA only
        aio_enabled = settings.get("aio_enabled", False)
        if aio_enabled and settings.get("aio_username", "").strip() and settings.get("aio_key", "").strip():
            aio_interval = max(15, int(settings.get("aio_interval_sec", 60) or 60))
            if energy_mode:
                aio_interval = aio_interval * ENERGY_LP_AIO_MULT
            if now - last_aio_send > aio_interval:
                last_aio_send = now
                publish_to_adafruit_io()

    # If we're in AP mode but the HTTP socket died, restart it.
    try:
        if wifi_mode == WIFI_MODE_AP and http_server_sock is None:
            start_http_server()
    except Exception:
        pass

    # Background STA auto-reconnect: if startup STA failed, retry every 90 s so
    # the device connects once the router becomes reachable (e.g. after reboot).
    # Stops after _STA_AUTO_RETRY_MAX attempts to avoid looping forever.
    # Cleared when the user manually holds D2 to stay in AP mode.
    if (wifi_mode == WIFI_MODE_AP and _sta_fallback
            and _sta_auto_retry_count < _STA_AUTO_RETRY_MAX
            and (settings.get("sta_ssid") or "").strip()):
        if (now - last_sta_auto_retry) >= _STA_AUTO_RETRY_INTERVAL:
            last_sta_auto_retry = now
            _sta_auto_retry_count += 1
            show_status("WiFi: connecting...")
            if switch_to_sta():
                _sta_fallback = False
                _sta_auto_retry_count = 0
            else:
                switch_to_ap()

    # If a graph redraw has been scheduled, perform it once the main loop is otherwise idle.
    if graph_refresh_needed and screen == SCREEN_MAIN and display_mode == 2 and (not graph_drawing):
        try:
            redraw_graph()
        except Exception as e:
            log('graph', 'Graph redraw error:', e, min_interval=2.0)
        # Clear the request flag; new samples or mode changes will set it again.
        graph_refresh_needed = False

    # Display dimming schedule (checks every 60 s, requires NTP)
    if (now - last_dim_check) >= 60.0:
        last_dim_check = now
        if settings.get("dim_enabled", False) and ntp_synced:
            try:
                import rtc as _rtc_dim
                hour = _rtc_dim.RTC().datetime.tm_hour
                start_h = int(settings.get("dim_start_hour", 22) or 22)
                end_h = int(settings.get("dim_end_hour", 7) or 7)
                dim_pct = max(0, min(100, int(settings.get("dim_brightness", 10) or 10)))
                # Handle overnight ranges (e.g. 22–7)
                if start_h > end_h:
                    in_dim = (hour >= start_h) or (hour < end_h)
                else:
                    in_dim = start_h <= hour < end_h
                target_brightness = (dim_pct / 100.0) if in_dim else 1.0
                # LP mode takes the lower of dim schedule and LP brightness.
                if energy_mode:
                    target_brightness = min(target_brightness, ENERGY_LP_BRIGHTNESS)
                try:
                    board.DISPLAY.brightness = target_brightness
                except Exception:
                    pass
            except Exception:
                pass
        elif not settings.get("dim_enabled", False):
            # Restore full brightness only when dimming is off AND LP mode
            # is not active.  LP mode manages its own brightness via
            # apply_energy_mode() and must not be overridden here.
            if not energy_mode:
                try:
                    board.DISPLAY.brightness = 1.0
                except Exception:
                    pass

    # Memory maintenance + monitor (very low overhead)
    if (now - last_gc_ts) >= MEM_MONITOR_INTERVAL_S:
        try:
            gc.collect()
            free_mem = gc.mem_free()
            alloc = gc.mem_alloc()
        except Exception:
            free_mem = 0
            alloc = 0
        last_gc_ts = now
        mem_samples += 1
        if free_mem:
            if free_mem < mem_free_min:
                mem_free_min = free_mem
            if free_mem > mem_free_max:
                mem_free_max = free_mem
            if mem_samples == 1:
                mem_free_ema = float(free_mem)
            else:
                mem_free_ema = (0.2 * float(free_mem)) + (0.8 * mem_free_ema)

    # Flush deferred settings save once the deadline has passed.
    if _save_deferred_ts > 0.0 and now >= _save_deferred_ts:
        _save_deferred_ts = 0.0
        save_settings()

    handle_http_client()
    time.sleep(ENERGY_LP_SLEEP_S if energy_mode else 0.01)
