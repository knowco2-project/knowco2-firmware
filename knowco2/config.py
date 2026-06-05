# knowco2/config.py
# ----------------------------------------------------------------------
# Immutable configuration constants.
#
# Everything here is a fixed tunable that the firmware reads but never
# reassigns at runtime. Values that DO change while running (current
# thresholds, active colour scheme, current screen, etc.) live in
# state.py instead — keeping the two apart is what makes it safe to
# `from knowco2 import config` anywhere without surprises.
# ----------------------------------------------------------------------

# ── Splash ──────────────────────────────────────────────────────────
SPLASH_BMP = "/assets/splash.bmp"
SPLASH_SECONDS = 4
SPLASH_BG = 0xFFFFFF

# ── Memory monitor ──────────────────────────────────────────────────
MEM_MONITOR_INTERVAL_S = 20

# ── Sampling / windowing ────────────────────────────────────────────
SCD_MEASUREMENT_PERIOD = 5.0
WINDOW_SECONDS = 300.0
WINDOW_SAMPLES = int(WINDOW_SECONDS / SCD_MEASUREMENT_PERIOD) + 1

TREND_DEADBAND = 10.0
TREND_LOOKBACK_SECONDS = 150.0
STATUS_DURATION = 3.0

# ── Sensor freeze detection ─────────────────────────────────────────
SENSOR_FROZEN_WARN_SEC = 30.0
SENSOR_HARD_RESET_SEC = 90.0

# ── Sensor CRC / recovery ───────────────────────────────────────────
SCD_MAX_FAILS_BEFORE_RESET = 3
SCD_RESET_COOLDOWN_SEC = 2.0
SCD_MAX_RECOVERIES_BEFORE_RESET = 3
SCD_SAMPLE_TIMEOUT = 30.0

# ── Calibration parameter limits ────────────────────────────────────
ALTITUDE_MIN = 0
ALTITUDE_MAX = 10000
PRESSURE_MIN_NONZERO = 400
PRESSURE_MAX = 2000

# ── Networking timing ───────────────────────────────────────────────
STA_RECONNECT_COOLDOWN_S = 60.0
NTP_MIN_RETRY_S = 60.0
NTP_SYNC_INTERVAL = 6 * 60 * 60  # seconds
CLOUD_MAX_BACKOFF = 10 * 60
CLOUD_OK_TTL = 300.0
_STA_AUTO_RETRY_INTERVAL = 90.0
_STA_AUTO_RETRY_MAX = 10

# ── NTP ─────────────────────────────────────────────────────────────
NTP_HOSTS = ("time.cloudflare.com", "time.google.com", "pool.ntp.org")
NTP_PORT = 123
NTP_UNIX_DELTA = 2208988800  # seconds between 1900-01-01 and 1970-01-01

# ── CO2 alert thresholds (defaults; live values are in state.py) ────
LOW_THRESHOLD_DEFAULT = 800
MED_THRESHOLD_DEFAULT = 1200
ALERT_THRESHOLD_DEFAULT = 1500

# ── Colour schemes ──────────────────────────────────────────────────
# NORMAL: red/yellow/green traffic light.
# CB: Wong colour-blind-safe palette (sky-blue / amber / vermillion).
SCHEME_NORMAL = {"low": 0x00FF00, "med": 0xFFFF00, "alert": 0xFF0000}
SCHEME_CB = {"low": 0x56B4E9, "med": 0xE69F00, "alert": 0xD55E00}

# ── Low Power / Energy Saver mode ───────────────────────────────────
LP_A_HOLD_SECONDS = 2.0
ENERGY_LP_BRIGHTNESS = 0.20
ENERGY_LP_SLEEP_S = 0.05
ENERGY_LP_CLOUD_MULT = 5
ENERGY_LP_MQTT_MULT = 5
ENERGY_LP_AIO_MULT = 5

# ── Battery thresholds ──────────────────────────────────────────────
BATT_WARN_PCT = 15
BATT_CRIT_PCT = 5
BATT_BOOT_WARN_V = 3.20

# ── Graph / history ─────────────────────────────────────────────────
MAX_POINTS_DEFAULT = 1000
MAX_WEB_POINTS = 2000

# ── Persistence ─────────────────────────────────────────────────────
SETTINGS_FILE = "settings.json"

# ── Screens ─────────────────────────────────────────────────────────
SCREEN_MAIN = 0
SCREEN_APINFO = 1
SCREEN_REGULATORY = 2

# ── Wi-Fi modes ─────────────────────────────────────────────────────
WIFI_MODE_AP = "ap"
WIFI_MODE_STA = "sta"

# ── Button hold thresholds ──────────────────────────────────────────
D2_HOLD_SECONDS = 2.0
B_HOLD_SECONDS = 2.0
