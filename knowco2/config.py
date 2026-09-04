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

# Fast onboarding. Claim exchange and telemetry remain pinned to the compiled
# KnowCO2 API origin; a response may only confirm this exact origin.
CLOUD_ACTIVATION_BASE_URL = "https://api.knowco2.com"
CLOUD_ACTIVATION_PATH = "/v1/devices/activate"
CLOUD_ACTIVATION_TIMEOUT_S = 8
CLOUD_ACTIVATION_RETRY_MIN_S = 15
CLOUD_ACTIVATION_RETRY_MAX_S = 5 * 60
ONBOARDING_CONNECT_DELAY_S = 1.5

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

# ── OTA security ────────────────────────────────────────────────────
# OTA firmware writes require EITHER the admin password OR a live
# physical-presence unlock: hold buttons A + B together for
# OTA_UNLOCK_HOLD_SECONDS to open a window of OTA_UNLOCK_WINDOW_SECONDS.
OTA_UNLOCK_HOLD_SECONDS = 3.0
OTA_UNLOCK_WINDOW_SECONDS = 300.0

# ── Safe-mode auto-recovery ─────────────────────────────────────────
# safemode.py reboots out of safe mode up to RECOVERY_MAX_RETRIES
# consecutive times; code.py clears the counter after the device has
# been up for RECOVERY_STABLE_UPTIME_S. NVM bytes used for the counter:
RECOVERY_MAX_RETRIES = 5
RECOVERY_STABLE_UPTIME_S = 60
RECOVERY_NVM_MAGIC_IDX = 0
RECOVERY_NVM_COUNT_IDX = 1

# ── Cloud TLS memory self-heal ──────────────────────────────────────
# A TLS handshake on the ESP32-S3 needs several multi-KB contiguous
# allocations from the ESP-IDF *internal* heap. Below this
# largest-free-block threshold the handshake would fail — and a failed
# wrap_socket leaks the raw socket (connection_manager <= 3.1.8) — so
# we skip the attempt and clean up instead.
CLOUD_TLS_MIN_LARGEST_BLOCK = 24576   # bytes
# After this many CONSECUTIVE memory-class cloud failures, hard-reset
# the MCU (leaked LWIP sockets cannot be reclaimed from Python).
CLOUD_MEMERR_RESET_AFTER = 5
# ...but never within this many seconds of boot (reboot-loop guard;
# safemode.py's NVM retry counter is the second backstop).
CLOUD_MEMERR_RESET_MIN_UPTIME_S = 600
# ...and never while the web UI was used within this many seconds
# (protects in-flight OTA uploads from the self-heal reboot).
CLOUD_MEMERR_RESET_WEB_GRACE_S = 300
# Pause before the single in-send retry after a mem-class failure;
# gives LWIP a moment to reclaim TIME_WAIT socket memory (RC-49).
CLOUD_MEM_RETRY_PAUSE_S = 3
