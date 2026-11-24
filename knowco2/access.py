# knowco2/access.py
# ----------------------------------------------------------------------
# Who may change the device.
#
# Two independent proofs exist:
#   * PHYSICAL PRESENCE — holding buttons A + B opens a short window and
#     puts a one-time code on the screen. Firmware installs and factory
#     reset must quote that code, so only the person who can see the device
#     can use the window (other hosts on the same network cannot).
#   * ADMIN PASSWORD — optional, stored salted+hashed, rate limited.
#
# A password never replaces physical presence for firmware installs; when
# one is configured it is required IN ADDITION.
#
# This module only reads/writes `state`; it has no hardware imports, so it
# is unit-testable on a workstation.
# ----------------------------------------------------------------------

import time

from . import state, config, crypto
from .helpers import rand_digits


# ── Physical-presence window ─────────────────────────────────────────
def open_unlock_window():
    """A + B hold. Open the window, issue a fresh one-time code (returned so
    the caller can show it on the screen — it is never sent over the
    network), and clear any password lockout: the person holding the device
    outranks anyone guessing from the network."""
    state.ota_unlock_until = time.monotonic() + config.OTA_UNLOCK_WINDOW_SECONDS
    state.ota_unlock_code = rand_digits(config.OTA_CODE_DIGITS)
    state.ota_code_msg = "OTA code " + state.ota_unlock_code
    state.ota_code_failures = 0
    state.admin_fail_count = 0
    state.admin_lock_until = 0.0
    return state.ota_unlock_code


def close_unlock_window():
    state.ota_unlock_until = 0.0
    state.ota_unlock_code = ""
    state.ota_code_failures = 0


def service_code_display(label, now):
    """Main-loop hook: keep the one-time code on the status line for as long
    as the window is open. Other status messages still get their normal few
    seconds (they set state.status_timeout); the code comes back when they
    expire and is removed the moment the window closes. Allocation-free on
    the common path."""
    if state.ota_unlock_code and now < state.ota_unlock_until:
        if state.status_timeout == 0.0:
            if label.text != state.ota_code_msg:
                label.text = state.ota_code_msg
            state.ota_code_shown = True
        return
    if state.ota_code_shown:
        state.ota_code_shown = False
        if state.status_timeout == 0.0 and label.text == state.ota_code_msg:
            label.text = ""
    if state.ota_unlock_code:
        close_unlock_window()  # expired by time


def unlock_active():
    """True while the physical-presence window (A + B hold) is open."""
    try:
        return bool(state.ota_unlock_until) and \
            time.monotonic() < state.ota_unlock_until
    except Exception:
        return False


def unlock_code_ok(provided):
    """True only inside the window and only for the code on the screen.
    Spaces/dashes in the typed code are ignored. Wrong codes are counted and
    too many close the window (a new A + B hold is then required)."""
    if not unlock_active():
        return False
    code = state.ota_unlock_code or ""
    typed = "".join(ch for ch in str(provided or "") if "0" <= ch <= "9")
    if not code or not typed:
        return False
    if crypto.constant_time_equals(typed, code):
        return True
    state.ota_code_failures += 1
    if state.ota_code_failures >= config.OTA_CODE_MAX_ATTEMPTS:
        close_unlock_window()
    return False


# ── Admin password ───────────────────────────────────────────────────
def admin_password_set():
    return bool(state.settings.get("admin_password", "") or "")


def admin_lock_remaining():
    """Seconds until password checks are accepted again (0 = not locked)."""
    try:
        remaining = state.admin_lock_until - time.monotonic()
    except Exception:
        return 0
    return int(remaining) + 1 if remaining > 0 else 0


def check_admin_password(provided):
    """Constant-time check of `provided` against the configured admin
    password. Returns False when no admin password is set, when nothing was
    provided, and while the guessing lockout is running."""
    stored = state.settings.get("admin_password", "") or ""
    if not stored or not provided:
        return False
    now = time.monotonic()
    if now < state.admin_lock_until:
        return False
    if crypto.verify_admin_password(provided, stored):
        state.admin_fail_count = 0
        return True
    state.admin_fail_count += 1
    over = state.admin_fail_count - config.ADMIN_LOCKOUT_AFTER
    if over >= 0:
        lock = config.ADMIN_LOCKOUT_BASE_S * (2 ** min(over, 4))
        if lock > config.ADMIN_LOCKOUT_MAX_S:
            lock = config.ADMIN_LOCKOUT_MAX_S
        state.admin_lock_until = now + lock
    return False
