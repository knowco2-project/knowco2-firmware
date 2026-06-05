# knowco2/state.py
# ----------------------------------------------------------------------
# Shared runtime state.
#
# WHY THIS MODULE EXISTS
# ----------------------
# The original single-file firmware kept ~80 module-level variables and
# mutated them from functions using the `global` keyword. That works in
# one file. It does NOT work once the code is split across modules,
# because Python's `global` only ever refers to the *current module's*
# namespace — a `global last_co2` inside sensor.py and a `global last_co2`
# inside ui.py refer to two different variables.
#
# The fix is to keep all cross-module mutable state as attributes on ONE
# module that everybody imports:
#
#     from knowco2 import state
#     ...
#     state.last_co2 = co2            # write
#     if state.last_co2 is not None:  # read
#
# Because `state` is a singleton module object, every other module sees
# the same values. Migrating a function from the old file is mechanical:
#   * delete its `global X, Y` line
#   * prefix each shared name with `state.`  (state.X, state.Y)
#   * leave purely-local variables untouched
#
# Constants that never change at runtime live in config.py, not here.
# ----------------------------------------------------------------------

from . import config

# ── Live CO2 alert thresholds (start at defaults, changed via settings) ──
LOW_THRESHOLD = config.LOW_THRESHOLD_DEFAULT
MED_THRESHOLD = config.MED_THRESHOLD_DEFAULT
ALERT_THRESHOLD = config.ALERT_THRESHOLD_DEFAULT

# Active colour scheme (swapped by apply_color_scheme()).
active_scheme = config.SCHEME_NORMAL

# Live graph history cap (changed via settings).
MAX_POINTS = config.MAX_POINTS_DEFAULT

# ── Settings dict (loaded from settings.json) ───────────────────────
settings = {}

# ── Filesystem writability (CIRCUITPY is read-only while USB-mounted) ─
fs_readonly = False
fs_warned = False

# ── Diagnostics surfaced in /status ─────────────────────────────────
import time as _time
boot_time_mono = _time.monotonic()
mem_free_min = 1_000_000_000
mem_free_max = 0
mem_free_ema = 0.0
mem_samples = 0
last_gc_ts = 0.0

# ── UI / display state ──────────────────────────────────────────────
screen = config.SCREEN_MAIN
temp_mode = "F"
display_mode = 0
alerts_enabled = True
graph_scale_mode = "fixed"
status_timeout = 0.0
alert_triggered = False
graph_drawing = False
graph_refresh_needed = False
sensor_frozen_shown = False
rate_of_change = None

# ── Latest sensor readings ──────────────────────────────────────────
last_co2 = None
last_co2_prev = None
last_temp_c = None
last_rh = None
co2_history = []

# ── Sensor object + health counters ─────────────────────────────────
sensor = None                 # CO2Sensor instance (was `scd`)
sensor_model_str = "SCD"
scd_serial_str = None
scd_init_failed = False
sensor_warned = False
scd_crc_failures = 0
scd_recoveries = 0
last_scd_reset = 0.0
last_scd_sample_ts = 0.0

# ── Button edge-tracking ────────────────────────────────────────────
prev_a = False
prev_b = False
prev_c = False
d2_hold_start = None
d2_hold_fired = False
_btn_a_hold_start = None
_btn_a_hold_fired = False
_btn_b_hold_start = None
_btn_b_hold_fired = False
_btn_b_pending = False

# ── Energy / low-power mode ─────────────────────────────────────────
energy_mode = False
_scd_period_effective = config.SCD_MEASUREMENT_PERIOD
_save_deferred_ts = 0.0

# ── Battery / fuel gauge ────────────────────────────────────────────
fuel_gauge = None
fuel_gauge_kind = None
fuel_bus_name = None
cached_vbat = None
cached_pct = None

# ── Identity ────────────────────────────────────────────────────────
hwid_hex = None
board_id_str = None
pair_code = None

# ── Networking: Wi-Fi / HTTP / mDNS ─────────────────────────────────
wifi_mode = config.WIFI_MODE_AP
http_server_sock = None
socket_pool = None
ip_str_cached = None
mdns_hostname = None
mdns_server = None
_wd = None                    # hardware watchdog handle

last_sta_reconnect_attempt = 0.0
_sta_fallback = False
_sta_auto_retry_count = 0
last_sta_auto_retry = 0.0

# ── QR code widgets / rebuild cache ─────────────────────────────────
qr_tilegrid_wifi = None
qr_tilegrid_url = None
qr_caption1 = None
qr_caption2 = None
qr_page_indicator = None
_qr_page = 0
_last_wifi_payload = None
_last_url_payload = None
_last_qr_page = None
_last_qr_target_modules = None
_last_qr_scale = None
_last_qr_right_x = None

# ── NTP ─────────────────────────────────────────────────────────────
ntp_synced = False
ntp_sync_pending = True
last_ntp_sync = 0.0
last_ntp_attempt = 0.0

# ── Cloud upload ────────────────────────────────────────────────────
cloud_enabled = False
cloud_api_url = ""
cloud_device_token = ""
cloud_interval_sec = 60
last_cloud_send = 0.0
cloud_failures = 0
cloud_last_ok = 0.0
cloud_last_http = None
cloud_last_error = ""
cloud_last_attempt_ts = 0

# Reusable TLS context + requests Session (kept alive to avoid socket leaks).
# Invalidated by Wi-Fi mode switches so a fresh one is built per network.
cloud_session = None
cloud_ctx = None

# ── MQTT / Adafruit IO timing ───────────────────────────────────────
last_mqtt_send = 0.0
last_aio_send = 0.0
mqtt_discovery_sent = False
