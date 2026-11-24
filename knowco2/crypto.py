# knowco2/crypto.py
# ----------------------------------------------------------------------
# Cryptographic helpers for cloud HMAC authentication.
# Pure functions; no shared state. Works with the stdlib hmac/hashlib when
# present, and falls back to a manual HMAC-SHA256 otherwise (some builds
# ship hashlib but not hmac).
# ----------------------------------------------------------------------

import binascii

try:
    import hmac
    import hashlib
    _HAS_HMAC = True
except Exception:
    _HAS_HMAC = False
    try:
        import adafruit_hashlib as hashlib
    except Exception:
        hashlib = None


def constant_time_equals(a, b):
    """Constant-time equality for passwords/tokens (str or bytes).
    Uses hmac.compare_digest when available; otherwise a manual
    XOR-accumulate compare. Only the length can leak via timing."""
    try:
        if isinstance(a, str):
            a = a.encode("utf-8")
        if isinstance(b, str):
            b = b.encode("utf-8")
    except Exception:
        return False
    if _HAS_HMAC and hasattr(hmac, "compare_digest"):
        try:
            return hmac.compare_digest(a, b)
        except Exception:
            pass
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= x ^ y
    return r == 0


def sha256_bytes(data):
    """SHA-256 digest of *data* (bytes), or None when no hash backend exists."""
    if hashlib is None:
        return None
    try:
        return hashlib.sha256(data).digest()
    except Exception:
        return None


# Stored admin-password format: "kc2h1$<salt hex>$<sha256(salt + password) hex>".
# The settings key stays "admin_password", so every "is one configured?" check
# keeps working and an older firmware fails CLOSED after a downgrade (it would
# compare against the hash string, never against an empty value).
ADMIN_HASH_PREFIX = "kc2h1$"


def is_hashed_admin_password(stored):
    return isinstance(stored, str) and stored.startswith(ADMIN_HASH_PREFIX)


def hash_admin_password(password):
    """Return the salted storage form of *password*. Falls back to the
    password itself when hashing is unavailable (never weaker than before)."""
    if not password:
        return ""
    try:
        import os
        salt = binascii.hexlify(os.urandom(8)).decode("ascii")
        digest = sha256_bytes((salt + password).encode("utf-8"))
        if digest is None:
            return password
        return ADMIN_HASH_PREFIX + salt + "$" + binascii.hexlify(digest).decode("ascii")
    except Exception:
        return password


def verify_admin_password(provided, stored):
    """Constant-time check of *provided* against a stored admin password in
    either the salted-hash form or the legacy plaintext form."""
    if not stored or not provided:
        return False
    if is_hashed_admin_password(stored):
        parts = stored.split("$")
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return False
        try:
            digest = sha256_bytes((parts[1] + provided).encode("utf-8"))
        except Exception:
            return False
        if digest is None:
            return False
        return constant_time_equals(
            binascii.hexlify(digest).decode("ascii"), parts[2])
    return constant_time_equals(provided, stored)


def b64encode_bytes(raw):
    try:
        import base64
        return base64.b64encode(raw).decode("utf-8")
    except Exception:
        # binascii.b2a_base64 adds a trailing newline; strip it
        return binascii.b2a_base64(raw).decode("utf-8").strip()


def decode_token_to_bytes(token_str):
    """Decode a base64 / urlsafe-base64 token. GET params can turn '+' into
    ' ', so we repair that before decoding."""
    if not token_str:
        return None
    t = token_str.strip().replace(" ", "+")
    t = t.replace("-", "+").replace("_", "/")
    while len(t) % 4 != 0:
        t += "="
    try:
        return binascii.a2b_base64(t)
    except Exception:
        return None


def hmac_sha256_digest(key_bytes, msg_bytes):
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
