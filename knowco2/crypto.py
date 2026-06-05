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
