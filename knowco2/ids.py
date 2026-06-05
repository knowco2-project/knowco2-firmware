# knowco2/ids.py
# ----------------------------------------------------------------------
# Device identity: hardware UID, board id, pairing code, mDNS hostname.
# (The sensor serial number now comes from the sensor driver's
# read_serial(), so it no longer lives here.)
# ----------------------------------------------------------------------

import binascii

from . import state
from .helpers import rand_token, rand_safe32

try:
    import microcontroller
except Exception:
    microcontroller = None

try:
    import board
except Exception:
    board = None


def init_ids():
    try:
        state.board_id_str = getattr(board, "board_id", None)
    except Exception:
        state.board_id_str = None

    try:
        if microcontroller is not None and hasattr(microcontroller.cpu, "uid"):
            state.hwid_hex = binascii.hexlify(microcontroller.cpu.uid).decode("utf-8").upper()
        else:
            state.hwid_hex = None
    except Exception:
        state.hwid_hex = None


def init_pair_code():
    base = (state.hwid_hex or rand_token(4))
    tail = base[-6:] if len(base) >= 6 else base
    state.pair_code = (tail + rand_safe32(2))[:8]


def init_mdns_hostname():
    # DNS-safe, short + readable: knowco2-xxxx
    base = (state.hwid_hex or state.pair_code or rand_token(4))
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
