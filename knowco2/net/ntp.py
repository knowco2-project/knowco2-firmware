# knowco2/net/ntp.py
# ----------------------------------------------------------------------
# NTP time sync (STA mode only). Best-effort: sets the RTC from the first
# server that answers. Rate-limiting/scheduling is handled by the main loop.
# ----------------------------------------------------------------------

import time

import rtc

from .. import state, config, runtime
from ..helpers import log

try:
    import wifi
    import socketpool
except ImportError:
    wifi = None
    socketpool = None


def _query_once(host, timeout=1.5):
    """Return unix epoch seconds (UTC) from an NTP server, or None."""
    if wifi is None or socketpool is None:
        return None
    sock = None
    try:
        if state.socket_pool is None:
            state.socket_pool = socketpool.SocketPool(wifi.radio)
        pool = state.socket_pool
        sock = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
        except Exception:
            pass

        # 48-byte NTP request: LI=0, VN=3, Mode=3 -> 0x1B
        req = bytearray(48)
        req[0] = 0x1B

        addr = pool.getaddrinfo(host, config.NTP_PORT)[0][-1]
        sock.sendto(req, addr)

        resp = bytearray(48)
        n = sock.recv_into(resp, 48)
        if not n or n < 48:
            return None

        # Transmit Timestamp seconds at bytes 40..43 (big-endian).
        secs = (resp[40] << 24) | (resp[41] << 16) | (resp[42] << 8) | resp[43]
        if secs == 0:
            return None
        unix = int(secs - config.NTP_UNIX_DELTA)
        if unix < 1577836800:  # sanity: after 2020-01-01
            return None
        return unix
    except Exception as e:
        log("ntp_err", "NTP query failed:", host, e, min_interval=10.0)
        return None
    finally:
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass


def ntp_sync(force=False):
    """Set RTC from NTP when on STA and connected."""
    if state.wifi_mode != config.WIFI_MODE_STA:
        return False
    if wifi is None:
        return False
    try:
        if not wifi.radio.connected:
            return False
    except Exception:
        return False

    now_mono = time.monotonic()
    if (not force) and state.ntp_synced and (now_mono - state.last_ntp_sync) < config.NTP_SYNC_INTERVAL:
        return True

    for host in config.NTP_HOSTS:
        unix = _query_once(host)
        if unix is None:
            continue
        try:
            rtc.RTC().datetime = time.localtime(unix)
            state.ntp_synced = True
            state.last_ntp_sync = now_mono
            state.ntp_sync_pending = False
            runtime.show_status("Time sync: OK")
            return True
        except Exception as e:
            log("ntp_set", "RTC set failed:", e, min_interval=10.0)

    state.last_ntp_sync = now_mono
    if not state.ntp_synced:
        runtime.show_status("Time sync: fail")
    return False
