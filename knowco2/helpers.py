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

# Human-friendly alphabet: no 0/O and no 1/I/L. That leaves 31 symbols, so
# tokens are drawn by rejection sampling (a plain `byte & 31` lookup needs 32
# entries and previously repeated "U", which biased the output).
SAFE31 = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
SAFE32 = SAFE31  # legacy name kept for external tooling


def rand_token(nbytes=4):
    """Uppercase hex token, e.g. '9F3A'."""
    return binascii.hexlify(os.urandom(nbytes)).decode("utf-8").upper()


def rand_safe32(n=8):
    """Human-readable token (no 0/O/1/I/L confusion), uniform over SAFE31."""
    out = []
    while len(out) < n:
        for bb in os.urandom(n):
            v = bb & 31
            if v < 31 and len(out) < n:
                out.append(SAFE31[v])
    return "".join(out)


def rand_digits(n=6):
    """Uniform decimal code, e.g. '048213' (used for the on-screen unlock code)."""
    out = []
    while len(out) < n:
        for bb in os.urandom(n):
            if bb < 250 and len(out) < n:  # 250 = 25 * 10 -> no modulo bias
                out.append(chr(48 + (bb % 10)))
    return "".join(out)
