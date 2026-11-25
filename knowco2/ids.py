# knowco2/ids.py
# ----------------------------------------------------------------------
# Device identity: hardware UID, board id, and mDNS hostname.
# (The sensor serial number now comes from the sensor driver's
# read_serial(), so it no longer lives here.)
# ----------------------------------------------------------------------

import binascii

from . import state, version
from .helpers import rand_token

try:
    import microcontroller
except Exception:
    microcontroller = None

try:
    import board
except Exception:
    board = None


def _application_hwid(uid, board_id, cp_version):
    if not uid or not any(uid):
        return None
    # CircuitPython PR #11494 changed each factory MAC byte from the old
    # nibble order to canonical order starting in 11.0.0-alpha.2. Our mDNS
    # name, HA discovery IDs and cloud hardware_id already use the old form.
    # Keep that public ID stable; leave the native UID / USB serial alone.
    if (board_id == "adafruit_feather_esp32s3_reverse_tft" and len(uid) == 6
            and cp_version.startswith("11.")
            and cp_version not in ("11.0.0-alpha.0", "11.0.0-alpha.1")):
        uid = bytes(((b & 15) << 4) | (b >> 4) for b in uid)
    return binascii.hexlify(uid).decode("utf-8").upper()


def init_ids():
    try:
        state.board_id_str = getattr(board, "board_id", None)
    except Exception:
        state.board_id_str = None

    try:
        if microcontroller is not None and hasattr(microcontroller.cpu, "uid"):
            state.hwid_hex = _application_hwid(
                microcontroller.cpu.uid, state.board_id_str, version.CP_VERSION
            )
        else:
            state.hwid_hex = None
    except Exception:
        state.hwid_hex = None


def init_mdns_hostname():
    # DNS-safe, short + readable: knowco2-xxxx
    base = (state.hwid_hex or rand_token(4))
    suffix = (base[-4:] if len(base) >= 4 else base).lower()
    state.mdns_hostname = ("knowco2-" + suffix).replace("_", "-")


def friendly_mdns_label(hostname, max_len=64):
    """Readable mDNS URL for display. Does not rename/alias the hostname."""
    if not hostname:
        return None
    s = hostname + ".local"
    if max_len and len(s) > max_len:
        return s[: max_len - 1] + "\u2026"
    return s
