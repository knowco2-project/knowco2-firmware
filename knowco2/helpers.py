# knowco2/helpers.py
# ----------------------------------------------------------------------
# Small, dependency-free utilities used across the firmware.
#
# Nothing here touches hardware or shared runtime state, so this module
# is safe to import from anywhere (including unit tests on a desktop).
# ----------------------------------------------------------------------

import time

LOG_ENABLED = True
_LOG_LAST = {}


def log(key, *args, min_interval=5.0):
    """Rate-limited print. Repeated logs under the same `key` are dropped
    until `min_interval` seconds have passed, so a tight failure loop can't
    flood the serial console or stall the main loop."""
    if not LOG_ENABLED:
        return
    now = time.monotonic()
    last = _LOG_LAST.get(key, 0)
    if (now - last) < min_interval:
        return
    _LOG_LAST[key] = now
    print(key + ":", *args)


def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value, min_val, max_val, default):
    iv = as_int(value, default)
    if iv is None:
        return default
    if iv < min_val:
        return min_val
    if iv > max_val:
        return max_val
    return iv


def safe_setattr(obj, name, value):
    try:
        setattr(obj, name, value)
        return True
    except Exception:
        return False


def safe_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return None


# ── Random tokens (device IDs, AP credentials) ──────────────────────
import os
import binascii

# Crockford-ish base32 alphabet (no easily-confused chars).
SAFE32 = "23456789ABCDEFGHJKMNPQRSTUVWXYZU"


def rand_token(nbytes=4):
    """Uppercase hex token, e.g. '9F3A'."""
    return binascii.hexlify(os.urandom(nbytes)).decode("utf-8").upper()


def rand_safe32(n=8):
    """Human-readable base32 token (no 0/O/1/I/L confusion)."""
    b = os.urandom(n)
    return "".join(SAFE32[bb & 31] for bb in b)
