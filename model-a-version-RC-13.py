# knowco2 firmware (AP portal + STA + mDNS)
# Target: Adafruit Feather ESP32-S3 Reverse TFT (CircuitPython 9.2.9)
# Version: RC-13  (fix OTA file upload body truncation)
# ----------------------------------------------------------------------
# FEATURE SUMMARY
# - Splash screen with centered logo bitmap and automatic cleanup.
# - CO₂/temperature/humidity sensing with SCD4x (periodic measurement).
# - Three main display modes: text summary, big CO₂, and live graph.
# - Graph history window with fixed/wide/auto scale, thresholds, and trend.
# - Button controls: A toggles °C/°F, B cycles display mode, C toggles
#   main/AP info screens (long-press switches Wi‑Fi mode).
# - Battery fuel‑gauge monitoring and percent/voltage display.
# - Alert thresholds with color-coded UI and status banner messaging.
# - STA/AP Wi‑Fi modes with QR codes for AP access and mDNS hostname.
# - HTTP configuration portal for Wi‑Fi, alerts, calibration, and device ID.
# - Settings persistence with safety checks for read‑only filesystems.
# - NTP time sync (STA) and optional HTTPS cloud upload with HMAC auth.
# - Memory monitor and status logging with throttling to avoid spam.
# - Sensor CRC failure tracking, stale-sample watchdog, recovery logic,
#   and MCU watchdog reset for hard stalls.
# RC-13 CHANGES
# - Fixed critical OTA file upload bug: _read_request_head reads in 512-byte
#   chunks and stops when it finds the \r\n\r\n header separator, but the
#   chunk containing the separator often includes the first ~50-100 bytes of
#   the file body.  Those bytes were silently discarded, causing the written
#   file to be truncated from the beginning and unparseable by CircuitPython.
#   Fix: _stream_request_body_to_file now splits headers_raw at \r\n\r\n,
#   writes the already-consumed body prefix first, then reads the remainder
#   from the socket.
# - Added firmware sanity check before install: before renaming code.py.ota
#   to code.py, the first 64 bytes are read and verified to start with a
#   valid Python token (#, import, from).  A corrupted or wrong file is
#   deleted and the existing firmware is left untouched.  This prevents
#   repeated CircuitPython crash-boot cycles that can trigger an automatic
#   filesystem reformat on the ESP32-S3.
# RC-12 CHANGES (carried forward)
# - Fixed language switcher: applyLang() no longer uses textContent on
#   elements that have child elements (inputs, selects).  It now updates
#   only the first text node, leaving child form elements intact.  This
#   was silently destroying the language <select> on every page load,
#   making it impossible to switch language at all.
# - Moved data-i18n off <label> onto an inner <span> so the pattern is
#   correct for all label elements that contain form controls.
# - Settings page now shows firmware version for easy OTA confirmation.
# RC-11 CHANGES (carried forward)
# - OTA firmware update now supports file upload (drag-and-drop or file
#   picker) in addition to the URL-based download.  File is streamed
#   directly to disk in 512-byte chunks so large firmware files never
#   overflow RAM.  URL download is also fixed to stream via
#   iter_content() rather than loading the full response into RAM.
# - Web UI internationalization: 8 language options (English, Spanish,
#   French, German, Portuguese, Italian, Japanese, Chinese Simplified).
#   Language is selected via a dropdown on the settings page, stored in
#   the browser's localStorage, and applied client-side via JavaScript.
#   No device reboot needed.  Device display remains English-only due to
#   the built-in font supporting ASCII characters only.
# - Security headers added to all HTTP responses:
#   X-Content-Type-Options, X-Frame-Options, Referrer-Policy.
#   Note: HTTPS for the local web server is not currently feasible in
#   CircuitPython — the ssl module only supports client-side TLS.
#   HTTP on the local LAN is acceptable; for secure remote access use a
#   VPN (Tailscale, WireGuard, etc.).
# RC-10 CHANGES (carried forward)
# - Settings and login forms now POST rather than GET, so passwords and
#   credentials are no longer visible in browser history or server logs.
#   GET parameter auth is still accepted for backward compat (API use).
# - CSV export: GET /export.csv returns all in-RAM history as a
#   downloadable CSV (seconds_ago, co2_ppm, temp_c, rh_pct).
# - MQTT publishing: configurable broker (host, port, user, pass, topic
#   prefix, interval).  Publishes CO₂/temp/RH on each interval (STA only).
#   Works with Home Assistant Mosquitto or any standard MQTT broker.
# - Adafruit IO publishing: separate AIO credentials (username + key),
#   configurable group key and interval.  Publishes to AIO feeds alongside
#   MQTT and cloud (all three can run simultaneously).
# - OTA firmware update: GET /update shows a form; POST /update accepts a
#   firmware_url parameter, fetches the file, writes to /code.py, and
#   soft-resets.  Requires admin password if set.  STA mode only.
# - Display dimming schedule: optional dim_enabled setting with
#   dim_start_hour, dim_end_hour (0-23) and dim_brightness (0-100%).
#   Requires NTP sync.  Checked every 60 s in the main loop.
# RC-9 CHANGES (carried forward)
# - boot.py: suppress CircuitPython REPL terminal text on the built-in TFT
#   as early as possible by setting display.root_group = displayio.Group()
#   and display.rotation = 180.  Users no longer see Adafruit boot text
#   or an upside-down display before the splash screen appears.
# RC-8 CHANGES (carried forward)
# - Persistent on-screen "SENSOR ERR" banner when sensor stops updating so
#   the user is always informed — no silent freezes.
# - Hard MCU reset after SENSOR_HARD_RESET_SEC (90 s) of no sensor data,
#   independent of recovery-attempt counts.  Prevents devices from running
#   indefinitely with a dead sensor.
# - NTP retry throttle: attempts are limited to once per NTP_MIN_RETRY_S
#   (60 s) when they fail, preventing the main loop from stalling on
#   repeated NTP calls every iteration.
# - STA reconnect cooldown (STA_RECONNECT_COOLDOWN_S = 60 s): consecutive
#   wifi.radio.connect() calls are spaced out to prevent long blocking
#   periods that can interfere with sensor polling.
# - Hardware watchdog timeout raised to 20 s (from 12 s) to accommodate
#   legitimate WiFi connect operations without spurious resets.
# - Watchdog feed inserted in ensure_sta_connected() and scd_recover() so
#   blocking I2C / WiFi ops don't trigger unwanted watchdog resets.
# - Cloud session cleared on WiFi mode switch, preventing stale socket
#   handles that could block cloud uploads after reconnect.
# - log() function restored to actually print throttled output.
# - SCD_SAMPLE_TIMEOUT raised to 30 s and SCD_MAX_RECOVERIES to 3.
# ----------------------------------------------------------------------

import time
import board
import displayio
import terminalio
import digitalio
import json
import os
import binascii

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
            display.root_group = None
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


# ======================================================================
#  CONFIG & CONSTANTS
# ======================================================================

SCD_MEASUREMENT_PERIOD = 5.0
WINDOW_SECONDS = 300.0
WINDOW_SAMPLES = int(WINDOW_SECONDS / SCD_MEASUREMENT_PERIOD) + 1

TREND_DEADBAND = 10.0
TREND_LOOKBACK_SECONDS = 150.0
STATUS_DURATION = 3.0

# RC-8: Sensor freeze detection and hard-reset thresholds
# After SENSOR_FROZEN_WARN_SEC with no new data, show the persistent "SENSOR ERR" banner.
# After SENSOR_HARD_RESET_SEC with no new data, force an MCU reset regardless of
# how many soft-recovery attempts have been made — this is the last resort.
SENSOR_FROZEN_WARN_SEC = 30.0
SENSOR_HARD_RESET_SEC = 90.0

# RC-8: STA reconnect cooldown — prevents wifi.radio.connect() from being called
# on every main-loop iteration when the network is unavailable.  A minimum of
# STA_RECONNECT_COOLDOWN_S seconds must elapse between connection attempts.
STA_RECONNECT_COOLDOWN_S = 60.0

# RC-8: NTP retry throttle — when NTP sync fails, wait at least this many seconds
# before the next attempt.  Without this guard the main loop calls ntp_sync() on
# every iteration (because ntp_sync_pending stays True), each blocking up to 4.5 s.
NTP_MIN_RETRY_S = 60.0

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

# RC-8: STA reconnect cooldown tracking
last_sta_reconnect_attempt = 0.0

# RC-8: NTP retry throttle tracking (separate from last_ntp_sync which only
# updates on success; last_ntp_attempt updates on every attempt)
last_ntp_attempt = 0.0

# RC-8: sensor frozen banner state
sensor_frozen_shown = False

# Track whether a CO₂ alert has already been shown. This prevents repeatedly
# re-triggering the alert status message on every sample when the CO₂
# concentration stays above the threshold. It is reset when the value
# falls below the threshold.
alert_triggered = False

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
_wifi_bmp, _wifi_pal, _wifi_w, _wifi_h = _make_small_text_bitmap("WIFI")
_twifi_bmp, _twifi_pal, _twifi_w, _twifi_h = _make_small_text_bitmap("TWIFI")
_cloud_bmp, _cloud_pal, _cloud_w, _cloud_h = _make_small_text_bitmap("CLOUD")

# Create TileGrids for each small-text indicator and position them at
# the top-right of the display.  Their visibility is toggled in
# update_wifi_indicator().  The cloud indicator sits below the WiFi
# indicator.
wifi_text_grid = displayio.TileGrid(_wifi_bmp, pixel_shader=_wifi_pal,
                                    x=display.width - _wifi_w - 2,
                                    y=2)
wifi_text_grid_ntp = displayio.TileGrid(_twifi_bmp, pixel_shader=_twifi_pal,
                                        x=display.width - _twifi_w - 2,
                                        y=2)
cloud_text_grid = displayio.TileGrid(_cloud_bmp, pixel_shader=_cloud_pal,
                                     x=display.width - _cloud_w - 2,
                                     y=10)

wifi_text_grid.hidden = True
wifi_text_grid_ntp.hidden = True
cloud_text_grid.hidden = True

main_group.append(wifi_text_grid)
main_group.append(wifi_text_grid_ntp)
main_group.append(cloud_text_grid)


def show_status(msg):
    global status_timeout
    status_label.text = msg
    status_timeout = time.monotonic() + STATUS_DURATION

# RC-8: Persistent "SENSOR ERR" banner shown at the bottom of the main screen
# when the sensor has not produced a new reading for SENSOR_FROZEN_WARN_SEC.
# Unlike show_status(), this does NOT disappear after a timeout — it stays
# visible until the sensor recovers.  This ensures the user always knows when
# the device has stopped measuring, even if everything else looks normal.
sensor_frozen_label = label.Label(terminalio.FONT, text="!! SENSOR ERR", color=0xFF4400, scale=1)
sensor_frozen_label.anchor_point = (0.5, 1.0)
sensor_frozen_label.anchored_position = (display.width // 2, display.height - 2)
sensor_frozen_label.hidden = True
main_group.append(sensor_frozen_label)

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

    if "lang" in params and params["lang"] in ("en", "es", "fr", "de", "pt", "it", "ja", "zh"):
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
    # thresholds to the range [400, 5000] and enforce an ascending order
    # (low <= med <= alert).  If the user entered values out of order, they
    # will be sorted automatically.  This prevents nonsensical thresholds
    # that could cause weird behavior or crashes.
    try:
        low = int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))
        med = int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))
        alert = int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))
        vals = [low, med, alert]
        # Clamp each value to the allowed range
        vals = [max(400, min(5000, v)) for v in vals]
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
    scd = adafruit_scd4x.SCD4X(i2c)
    init_scd_serial(scd)

    status_label.text = "Warming up..."
    scd.start_periodic_measurement()
    time.sleep(5)
    status_label.text = "Measuring..."

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
        scd.set_ambient_pressure(int(ap_val))
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
# RC-8: increased from 2 to 3 — give the sensor one more soft-recovery
# chance before escalating to an MCU reset.
SCD_MAX_RECOVERIES_BEFORE_RESET = 3

# RC-8: raised from 20.0 to 30.0 s so that brief WiFi/NTP operations that
# temporarily delay sensor polling do not trigger a spurious recovery.
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
        # Stop periodic measurement (ignore errors)
        try:
            scd.stop_periodic_measurement()
            time.sleep(0.2)
        except Exception:
            pass
        # Soft reset the sensor
        try:
            scd.soft_reset()
            time.sleep(0.8)
        except Exception:
            pass
        # Restart periodic measurement
        try:
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
                graph_value_label.text = "%d ppm %s %+.1f ppm/s" % (int(last_co2), arrow, rate_of_change)
            else:
                graph_value_label.text = "%d ppm %s" % (int(last_co2), arrow)
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
    """Create/refresh the two QR codes on the AP info screen.

    This is relatively expensive (allocations + group edits). To prevent visible
    flicker, we rebuild ONLY when the encoded payload changes.
    """
    global qr_tilegrid_wifi, qr_tilegrid_url, qr_caption1, qr_caption2
    global _last_wifi_payload, _last_url_payload, _last_qr_target_modules, _last_qr_scale, _last_qr_right_x

    if adafruit_miniqr is None:
        return

    try:
        # Prefer mDNS URL when in STA (friendly + stable), else use IP.
        if wifi_mode == WIFI_MODE_STA and mdns_hostname:
            url_payload = "http://%s.local/" % mdns_hostname
        else:
            url_payload = build_url_qr_payload(ip_str)

        wifi_payload = build_wifi_qr_payload(ssid, pw)

        # If nothing changed, do nothing (prevents flashing).
        if wifi_payload == _last_wifi_payload and url_payload == _last_url_payload:
            return

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

        _last_wifi_payload = wifi_payload
        _last_url_payload = url_payload
        _last_qr_target_modules = target_modules
        _last_qr_scale = scale
        _last_qr_right_x = right_x
    except Exception as e:
        _last_wifi_payload = None
        _last_url_payload = None
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
    # Explicitly reference global indicator TileGrids so they are always
    # available in this scope. Without declaring them global, a NameError
    # may occur on some CircuitPython builds when these variables are
    # referenced before assignment.
    global wifi_text_grid, wifi_text_grid_ntp, cloud_text_grid
    # Hide all custom small-text indicators by default.
    wifi_text_grid.hidden = True
    wifi_text_grid_ntp.hidden = True
    cloud_text_grid.hidden = True
    # Determine if Wi-Fi is connected as a client (STA).  If so, show the
    # appropriate small-text indicator.  Use the version with a leading 'T'
    # when the RTC has been synced by NTP.
    try:
        if wifi is not None and wifi_mode == WIFI_MODE_STA and wifi.radio.connected:
            if ntp_synced:
                wifi_text_grid_ntp.hidden = False
            else:
                wifi_text_grid.hidden = False
    except Exception:
        pass
    # If a cloud upload succeeded recently, show the small-text "CLOUD" indicator.
    try:
        now = time.monotonic()
        cloud_ok_recent = (cloud_last_ok > 0.0) and ((now - cloud_last_ok) <= CLOUD_OK_TTL)
        if cloud_enabled and wifi_mode == WIFI_MODE_STA and cloud_ok_recent:
            cloud_text_grid.hidden = False
    except Exception:
        pass
    # Ensure the original text labels remain empty so they don't display anything.
    wifi_ind_label.text = ""
    cloud_ind_label.text = ""
def update_visibility():
    main_visible = (screen == SCREEN_MAIN)
    ap_visible = (screen == SCREEN_APINFO)

    # RC-8: sensor frozen banner is only shown on the main screen, and only when frozen.
    sensor_frozen_label.hidden = not (main_visible and sensor_frozen_shown)

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

            PIXELS_PER_SAMPLE = max(1, GRAPH_WIDTH // WINDOW_SAMPLES)
            used_width = min(GRAPH_WIDTH, n * PIXELS_PER_SAMPLE)
            left_blank = GRAPH_WIDTH - used_width

            # Horizontal grid lines for 25%, 50% and 75% of the graph height.
            for y in [int(GRAPH_HEIGHT * 0.25), int(GRAPH_HEIGHT * 0.5), int(GRAPH_HEIGHT * 0.75)]:
                if 0 <= y < GRAPH_HEIGHT:
                    for x in range(GRAPH_WIDTH):
                        graph_bitmap[x, y] = 1

            # Vertical grid lines every 20 pixels.
            for x in range(left_blank, GRAPH_WIDTH, 20):
                for yy in range(GRAPH_HEIGHT):
                    if graph_bitmap[x, yy] == 0:
                        graph_bitmap[x, yy] = 1

            latest_x = GRAPH_WIDTH - 1
            latest_y = None

            # Plot each CO₂ point as a vertical bar, using the alert thresholds for colours.
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

            # Mark the most recent point with a white dot.
            if latest_y is not None:
                for dy in (-1, 0, 1):
                    yy = latest_y + dy
                    if 0 <= yy < GRAPH_HEIGHT:
                        graph_bitmap[latest_x, yy] = 5

            # Update the threshold labels and axis labels based on the new range.
            _set_threshold_label_positions(low, high)
            update_axis_labels(low, high, span_seconds)
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
        except OSError:
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
            except OSError:
                time.sleep(0.01)
        return body
    except Exception:
        return b""

def _stream_request_body_to_file(conn, headers_raw, dest_path, max_bytes=400000, max_wait=60.0):
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

        written = 0
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
                try:
                    chunk = sock_recv(conn, min(512, remaining))
                    if not chunk:
                        time.sleep(0.01)
                        continue
                    f.write(chunk)
                    written += len(chunk)
                except OSError:
                    time.sleep(0.01)
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
<html>
<head>
  <meta charset="utf-8">
  <title>Know CO2 Settings</title>
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
    <h1>Know CO2 <span style="font-size:13px;font-weight:400;color:#555;vertical-align:middle;">RC-13</span></h1>
    <div class="muted" style="font-size:12px;margin-bottom:10px;">
      Open <span class="code">http://""" + ip_for_hint + """/"</span>.""" + mdns_hint + """
      <br><small class="muted">If your phone says “No Internet”, that’s expected during AP setup.</small>
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
        <label>Low threshold (ppm)
          <input type="number" name="low" min="400" max="5000"
                 value='""" + str(int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))) + """'>
        </label>
        <label>Medium threshold (ppm)
          <input type="number" name="med" min="400" max="5000"
                 value='""" + str(int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))) + """'>
        </label>
        <label>Alert threshold (ppm)
          <input type="number" name="alert" min="400" max="5000"
                 value='""" + str(int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))) + """'>
        </label>
        <label>Max history points in RAM
          <input type="number" name="max_points" min="100" max="50000"
                 value='""" + str(int(max_points)) + """'>
        </label>
        <small>Higher values show longer history but use more memory.</small>
      </fieldset>

      <h2>Password protection</h2>
      <fieldset>
        <legend>Password</legend>
        <label>Settings password
          <input type="password" name="admin_pw" maxlength="64" value="">
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

      <fieldset class="row">
        <legend>Alerts</legend>
        <label>
          <input type="checkbox" name="alerts" value="on" """ + checked_alerts + """>
          Enable color alerts and on-screen alert messages
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
                 value='""" + sta_ssid + """'>
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
        <legend data-i18n='sec_cloud'>API data ingest</legend>
        <small class="muted">Onboard your device at <a href=\"https://cloud.knowco2.com\">https://cloud.knowco2.com</a> register and generate a device id and secret to enter.</small>
        <label>
          <input type="checkbox" name="cloud_enabled" value="on" """ + cloud_enabled_checked + """>
          Enable cloud uploads (requires STA Wi-Fi + token)
        </label>

        <label>Cloud API URL
          <input type="text" name="cloud_api_url" maxlength="200"
                 placeholder="https://api.knowco2.com"
                 value='""" + cloud_api + """'>
        </label>

        <label>Device token (secret)
          <input type="password" name="cloud_device_token" maxlength="128" value="">
        </label>
        <small>Paste token once. It is stored on device and not shown again.</small>

        <label>Device ID
          <input type="text" name="device_id" maxlength="40"
                 value='""" + device_id + """'>
        </label>


        <label>Upload interval (seconds)
          <input type="number" name="cloud_interval_sec" min="15" max="3600"
                 value='""" + str(int(settings.get("cloud_interval_sec", 60))) + """'>
        </label>
        <small class="muted">
          Pairing: create an account, then enter this device's <b>Pair code</b>.
          The cloud app returns a device token you paste here.
        </small>
      </fieldset>

      <!-- Device identity section removed; Device ID is now under Cloud telemetry and local endpoints moved to bottom. -->

      <fieldset><legend data-i18n='sec_mqtt'>MQTT Broker (Home Assistant etc.)</legend>
        <label><input type='checkbox' name='mqtt_enabled' """ + mqtt_checked + """> Enable MQTT publishing</label>
        <label>Broker hostname/IP<br><input type='text' name='mqtt_broker' value='""" + mqtt_broker + """' placeholder='192.168.1.x or mqtt.example.com'></label>
        <label>Port<br><input type='number' name='mqtt_port' value='""" + mqtt_port + """' min='1' max='65535'></label>
        <label>Username (optional)<br><input type='text' name='mqtt_user' value='""" + mqtt_user + """'></label>
        <label>Password (optional)<br><input type='password' name='mqtt_pass' placeholder='leave blank to keep current'></label>
        <label>Topic prefix<br><input type='text' name='mqtt_topic_prefix' value='""" + mqtt_topic_prefix + """' placeholder='knowco2'></label>
        <label>Publish interval (seconds)<br><input type='number' name='mqtt_interval_sec' value='""" + mqtt_interval + """' min='15' max='3600'></label>
        <small>Topics: &lt;prefix&gt;/co2, &lt;prefix&gt;/temp_c, &lt;prefix&gt;/rh</small>
      </fieldset>
      <fieldset><legend data-i18n='sec_aio'>Adafruit IO</legend>
        <label><input type='checkbox' name='aio_enabled' """ + aio_checked + """> Enable Adafruit IO</label>
        <label>AIO Username<br><input type='text' name='aio_username' value='""" + aio_username + """'></label>
        <label>AIO Key<br><input type='password' name='aio_key' placeholder='leave blank to keep current'></label>
        <label>Feed group key<br><input type='text' name='aio_group_key' value='""" + aio_group + """' placeholder='knowco2'></label>
        <label>Publish interval (seconds)<br><input type='number' name='aio_interval_sec' value='""" + aio_interval + """' min='15' max='3600'></label>
        <small>Feeds: &lt;group&gt;.co2, &lt;group&gt;.temperature, &lt;group&gt;.humidity</small>
      </fieldset>
      <fieldset><legend data-i18n='sec_dim'>Display Dimming Schedule</legend>
        <label><input type='checkbox' name='dim_enabled' """ + dim_checked + """> Enable scheduled dimming (requires NTP)</label>
        <label>Dim start hour (0-23)<br><input type='number' name='dim_start_hour' value='""" + dim_start + """' min='0' max='23'></label>
        <label>Dim end hour (0-23)<br><input type='number' name='dim_end_hour' value='""" + dim_end + """' min='0' max='23'></label>
        <label>Brightness during dim period (0-100%)<br><input type='number' name='dim_brightness' value='""" + dim_brightness + """' min='0' max='100'></label>
        <small>Example: start=22, end=7 dims from 10 PM to 7 AM.</small>
      </fieldset>
      <p><a href='/update' style='color:#e53935;font-weight:600;'>&#8593; OTA Firmware Update</a></p>

      <div class="row">
        <button type="submit" data-i18n="save">Save settings</button>
      </div>
      <div class="row">
        <small>
          <b>Local endpoints:</b><br>
          • <code>GET /status</code> → live JSON status<br>
          • <code>GET /data</code> → CO₂ history JSON (up to """ + str(MAX_WEB_POINTS) + """ points)<br>
          • <code>GET /export.csv</code> → download CO₂ history as CSV
        </small>
      </div>
      <div class="row">
        <small>
          <a href="/calibration" style="color:#00bcd4;">Calibration</a> – adjust altitude/pressure and force calibration
        </small>
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
    const statusRateEl  = document.getElementById('status-rate');

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
    note_https:"Note: Local web server uses HTTP (HTTPS not supported in CircuitPython). Traffic stays on your LAN."
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
    note_https:"Nota: El servidor web usa HTTP. El trafico permanece en su red local."
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
    note_https:"Note: Le serveur web utilise HTTP. Le trafic reste sur votre reseau local."
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
    note_https:"Hinweis: Webserver nutzt HTTP. Datenverkehr bleibt im lokalen Netz."
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
    note_https:"Nota: Servidor web usa HTTP. Trafego fica na rede local."
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
    note_https:"Nota: Il server web usa HTTP. Il traffico resta sulla rete locale."
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
    note_https:"\u6ce8\u610f: \u30ed\u30fc\u30ab\u30eb\u30b5\u30fc\u30d0\u30fc\u306fHTTP\u3092\u4f7f\u7528\u3002\u901a\u4fe1\u306fLAN\u5185\u306b\u3068\u3069\u307e\u308a\u307e\u3059\u3002"
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
    note_https:"\u6ce8\u610f\uff1a\u672c\u5730\u670d\u52a1\u5668\u4f7f\u7528 HTTP\u3002\u6d41\u91cf\u4ec5\u5728\u5c40\u57df\u7f51\u5185\u3002"
  }
};
function applyLang(lang){
  var t = T[lang] || T['en'];
  document.querySelectorAll('[data-i18n]').forEach(function(el){
    var k = el.getAttribute('data-i18n');
    if (!t[k]) return;
    // Only set textContent on leaf nodes (no child elements).
    // Setting textContent on a parent would destroy its child inputs/selects.
    if (el.children.length === 0) {
      el.textContent = t[k];
    } else {
      // Update only the first text node, leaving child elements intact.
      for (var i = 0; i < el.childNodes.length; i++) {
        if (el.childNodes[i].nodeType === 3) {
          el.childNodes[i].nodeValue = t[k];
          break;
        }
      }
    }
  });
  document.querySelectorAll('[data-i18n-title]').forEach(function(el){
    var k = el.getAttribute('data-i18n-title');
    if (t[k]) el.title = t[k];
  });
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
        "cloud_api_url": cloud_api_url,
        "cloud_configured": bool(cloud_api_url) and bool(cloud_device_token),
        "cloud_last_attempt_ts": cloud_last_attempt_ts,
        "cloud_last_http": cloud_last_http,
        "cloud_last_error": cloud_last_error,
        # Instantaneous CO₂ rate of change (ppm per second)
        "rate_of_change": rate_of_change,
    }

    # Diagnostics (memory + uptime)
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


def render_calibration_page():
    """
    Generate the HTML for the calibration page.  This page allows the user
    to configure altitude and ambient pressure compensation, toggle
    Automatic Self Calibration (ASC), and perform a manual forced
    calibration against a reference CO₂ concentration.  It also
    displays the current calibration settings and the timestamp of the
    last calibration.
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
      <fieldset style='border:1px solid #333; border-radius:8px; padding:10px;'>
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
    """
    global settings
    scd_available = (scd is not None)
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
                _safe_call(scd.set_ambient_pressure, 0)
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
                    _safe_call(scd.set_ambient_pressure, int(pv))
            else:
                show_status("Sensor unavailable")
            # Persist changes
            save_settings()
            show_status("Calibration settings updated")
    # Render the calibration page
    html = render_calibration_page()
    header, body = make_html_response(html)
    send_all(conn, header)
    send_all(conn, body)

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
        tmp_path = "/code.py.ota"
        ok, msg = _stream_request_body_to_file(conn, raw_headers, tmp_path)
        if not ok:
            _send_ota_result(conn, False, "Upload failed: " + msg)
            try:
                import os as _os2
                _os2.remove(tmp_path)
            except Exception:
                pass
            return
        # Sanity-check: read the first 64 bytes and confirm it looks like a
        # Python source file before installing it.  A corrupted or truncated
        # upload will fail here rather than silently installing bad firmware
        # that could trigger CircuitPython's filesystem reformat on repeated
        # crashes.
        try:
            with open(tmp_path, "rb") as _f:
                _head = _f.read(64)
            _head_str = _head.lstrip(b"\xef\xbb\xbf")  # strip optional UTF-8 BOM
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
                    "Upload rejected: file does not appear to be valid Python "
                    "(first bytes: %r). Upload aborted — existing firmware unchanged." % _head[:16])
                return
        except Exception as check_err:
            _send_ota_result(conn, False, "Could not verify uploaded file: " + str(check_err))
            return
        # Rename into place
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
            # Stream response to disk to avoid RAM overflow on large files.
            with open(tmp_path, "wb") as f:
                try:
                    # iter_content available in adafruit_requests >= 4.x
                    for chunk in response.iter_content(chunk_size=512):
                        if chunk:
                            f.write(chunk)
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
.wrap{max-width:620px;margin:0 auto;padding:16px}
h1{color:#00bcd4;margin-top:8px}
h2{font-size:16px;color:#aaa;margin:20px 0 4px}
label{display:block;margin-top:10px;font-size:14px}
input[type=text],input[type=url]{width:100%;box-sizing:border-box;padding:8px;border-radius:4px;border:1px solid #444;background:#111;color:#eee;font-size:14px}
.drop-zone{border:2px dashed #444;border-radius:8px;padding:24px;text-align:center;cursor:pointer;transition:border-color .2s;margin-top:8px}
.drop-zone.dragover,.drop-zone:hover{border-color:#00bcd4}
.drop-zone input[type=file]{display:none}
.drop-zone p{margin:0;color:#aaa;font-size:14px}
#upload-btn,#url-btn{margin-top:12px;padding:8px 20px;border-radius:4px;border:1px solid #e53935;background:#e53935;color:#fff;font-weight:600;cursor:pointer;font-size:14px;display:inline-block}
#upload-btn:disabled,#url-btn:disabled{opacity:.5;cursor:not-allowed}
.warn{color:#ffb300;font-size:13px;margin-top:8px}
.progress{display:none;margin-top:8px;font-size:13px;color:#aaa}
a{color:#00bcd4}
hr{border:none;border-top:1px solid #333;margin:20px 0}
</style></head><body><div class='wrap'>
<h1>OTA Firmware Update</h1>
<p class='warn'>&#9888; This will overwrite the running firmware. Keep a backup. Ensure a stable WiFi connection.</p>

<h2>Option 1 — Upload a file from your computer</h2>
<div class='drop-zone' id='drop-zone' onclick="document.getElementById('fw-file-input').click()">
  <p>&#128196; Drag &amp; drop a <code>.py</code> firmware file here, or click to browse</p>
  <input type='file' id='fw-file-input' accept='.py,.txt'>
</div>
<div class='progress' id='upload-progress'>Uploading...</div>
<button id='upload-btn' disabled onclick='doUpload()'>Upload &amp; Install</button>

<hr>
<h2>Option 2 — Download from a URL (STA mode only)</h2>
<form method='POST' action='/update'>""" + pw_field + """
<label>Firmware URL<br><input type='url' name='firmware_url' placeholder='http://192.168.1.x/firmware.py'></label>
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

fi.addEventListener('change', function(){
  if (fi.files.length) { _file = fi.files[0]; ub.disabled = false; dz.querySelector('p').textContent = '\\u2713 ' + _file.name + ' (' + Math.round(_file.size/1024) + ' KB)'; }
});
dz.addEventListener('dragover', function(e){ e.preventDefault(); dz.classList.add('dragover'); });
dz.addEventListener('dragleave', function(){ dz.classList.remove('dragover'); });
dz.addEventListener('drop', function(e){
  e.preventDefault(); dz.classList.remove('dragover');
  if (e.dataTransfer.files.length){ _file = e.dataTransfer.files[0]; ub.disabled = false; dz.querySelector('p').textContent = '\\u2713 ' + _file.name + ' (' + Math.round(_file.size/1024) + ' KB)'; }
});

function doUpload(){
  if (!_file) return;
  if (!confirm('Install ' + _file.name + ' as firmware? The device will reboot.')) return;
  ub.disabled = true;
  pr.style.display = 'block';
  pr.textContent = 'Uploading ' + _file.name + '...';
  var url = '/update?upload=1' + (_pw ? '&pw=' + encodeURIComponent(_pw) : '');
  var xhr = new XMLHttpRequest();
  xhr.open('POST', url, true);
  xhr.setRequestHeader('Content-Type', 'application/octet-stream');
  xhr.onload = function(){
    pr.innerHTML = xhr.responseText;
  };
  xhr.onerror = function(){
    pr.textContent = 'Upload failed (network error)';
    ub.disabled = false;
  };
  xhr.upload.onprogress = function(e){
    if (e.lengthComputable) pr.textContent = 'Uploading... ' + Math.round(e.loaded/e.total*100) + '%';
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
        if method == b"POST":
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

    # RC-8: invalidate the cloud session so a new one is created for the new
    # network context.  Stale session handles cause blocking errors on cloud upload.
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
    # RC-8: guard against calling wifi.radio.connect() on every loop iteration
    # when the network is unavailable.  connect() can block for 10-30 s; without
    # a cooldown this starves the sensor-polling and NTP code paths.
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

    # RC-8: clear the cloud session and reset the reconnect cooldown so the
    # first connection attempt after switching modes happens immediately.
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

# ======================================================================
#  MQTT PUBLISHING
#  Supports generic MQTT broker (e.g. Home Assistant) and Adafruit IO.
#  Uses connect-publish-disconnect per interval to keep memory clean.
# ======================================================================

last_mqtt_send = 0.0
last_aio_send = 0.0

def _mqtt_publish_one(broker, port, user, password, topics_payloads, use_ssl=False):
    """Connect to an MQTT broker, publish a dict of {topic: payload_str}, disconnect.
    topics_payloads is a list of (topic, payload_str) tuples."""
    if not _HAS_MQTT or MQTT is None:
        return False
    if wifi is None or not wifi.radio.connected:
        return False
    pool = socketpool.SocketPool(wifi.radio) if socketpool is not None else None
    if pool is None:
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
        for topic, payload in topics_payloads:
            try:
                mqtt_client.publish(topic, payload)
            except Exception as e:
                print("MQTT publish error:", topic, e)
        mqtt_client.disconnect()
        return True
    except Exception as e:
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
        topics.append(("%s/co2" % prefix, str(int(last_co2))))
    if last_temp_c is not None:
        topics.append(("%s/temp_c" % prefix, "%.2f" % last_temp_c))
    if last_rh is not None:
        topics.append(("%s/rh" % prefix, "%.2f" % last_rh))
    if not topics:
        return False
    ok = _mqtt_publish_one(broker, port, user, password, topics)
    if ok:
        log("mqtt", "MQTT published to", broker, min_interval=30.0)
    else:
        log("mqtt_err", "MQTT publish failed to", broker, min_interval=30.0)
    return ok


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
        topics.append(("%s/feeds/%s.co2" % (aio_user, group), str(int(last_co2))))
    if last_temp_c is not None:
        topics.append(("%s/feeds/%s.temperature" % (aio_user, group), "%.2f" % last_temp_c))
    if last_rh is not None:
        topics.append(("%s/feeds/%s.humidity" % (aio_user, group), "%.2f" % last_rh))
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
last_dim_check = 0.0


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
        # RC-8: raised from 12 s to 20 s.  WiFi connect() can legitimately block
        # for up to ~15 s; the previous 12 s limit caused spurious resets.
        # The watchdog is now also fed explicitly before long blocking ops.
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

    # D0 (A) toggle temp mode on main screen
    if a_now and (not prev_a) and screen == SCREEN_MAIN:
        temp_mode = "C" if temp_mode == "F" else "F"
        settings["temp_mode"] = temp_mode
        save_settings()
        refresh_text()
        show_status("Temp: " + temp_mode)

    # D1 (B) cycle display mode
    if b_now and (not prev_b):
        if screen == SCREEN_APINFO:
            screen = SCREEN_MAIN
            update_visibility()
        if screen == SCREEN_MAIN:
            display_mode = (display_mode + 1) % 3
            settings["display_mode"] = display_mode
            save_settings()
            update_visibility()
            # If the user switched into graph mode, schedule a redraw rather than doing it immediately.
            if display_mode == 2:
                graph_refresh_needed = True
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
                # If returning to the main screen in graph mode, schedule a redraw
                if display_mode == 2:
                    graph_refresh_needed = True
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

    # Poll the sensor less often and handle CRC failures gracefully
    if now - last_sensor > 1.0:
        last_sensor = now
        if scd is None:
            if scd_init_failed and (not sensor_warned):
                show_status("Sensor unavailable")
                sensor_warned = True
        else:
            try:
                if scd.data_ready:
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
                        rate_of_change = (co2 - prev_co2) / SCD_MEASUREMENT_PERIOD
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
                _scd_age = time.monotonic() - last_scd_sample_ts
                if _scd_age > SCD_SAMPLE_TIMEOUT:
                    show_status("SCD: timeout")
                    scd_recover()
                    # Reset the last sample timestamp so we don't immediately
                    # trigger another recovery if the sensor is still warming up.
                    last_scd_sample_ts = time.monotonic()
            except Exception:
                # Don't let watchdog errors crash the loop; they will be logged
                pass

    # RC-8: update the persistent sensor-frozen banner on every main-loop
    # iteration so the user sees "!! SENSOR ERR" as soon as data goes stale
    # and the banner disappears as soon as the sensor recovers.
    try:
        _scd_age_now = time.monotonic() - last_scd_sample_ts
        _frozen = _scd_age_now > SENSOR_FROZEN_WARN_SEC
        if _frozen != sensor_frozen_shown:
            sensor_frozen_shown = _frozen
            sensor_frozen_label.hidden = not (screen == SCREEN_MAIN and sensor_frozen_shown)
    except Exception:
        pass

    # RC-8: hard MCU reset if the sensor has been frozen for longer than
    # SENSOR_HARD_RESET_SEC regardless of how many soft-recovery attempts
    # have been made.  This is the last-resort consumer-safety mechanism.
    # Without it, a device could run indefinitely with a dead sensor while
    # appearing operational to the user.
    try:
        if (time.monotonic() - last_scd_sample_ts) > SENSOR_HARD_RESET_SEC:
            show_status("SCD: hard reset")
            time.sleep(0.5)
            if microcontroller is not None:
                microcontroller.reset()
    except Exception:
        pass

    # NTP sync (STA only) - keep timestamps valid for cloud auth
    # RC-8: guard with a per-attempt cooldown (last_ntp_attempt) so that a
    # run of failed NTP queries does not stall the main loop.  Previously
    # ntp_sync_pending staying True caused a 3×1.5 s blocking NTP attempt on
    # every single main-loop iteration until NTP finally succeeded.
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
            if now - last_mqtt_send > mqtt_interval:
                last_mqtt_send = now
                publish_to_mqtt()

        # Adafruit IO publish (periodic) - STA only
        aio_enabled = settings.get("aio_enabled", False)
        if aio_enabled and settings.get("aio_username", "").strip() and settings.get("aio_key", "").strip():
            aio_interval = max(15, int(settings.get("aio_interval_sec", 60) or 60))
            if now - last_aio_send > aio_interval:
                last_aio_send = now
                publish_to_adafruit_io()

    # If we're in AP mode but the HTTP socket died, restart it.
    try:
        if wifi_mode == WIFI_MODE_AP and http_server_sock is None:
            start_http_server()
    except Exception:
        pass

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
                try:
                    board.DISPLAY.brightness = target_brightness
                except Exception:
                    pass
            except Exception:
                pass
        elif not settings.get("dim_enabled", False):
            # Restore full brightness if dimming was disabled
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

    handle_http_client()
    time.sleep(0.02)
