# knowco2/net/wifi.py
# ----------------------------------------------------------------------
# Wi-Fi mode switching (AP <-> STA) and mDNS advertisement.
#
# Reports progress and triggers UI/web actions through `runtime` hooks, so
# this module never imports the UI or web layers directly (no import cycle).
# Cloud session invalidation is done by clearing state.cloud_session /
# state.cloud_ctx — the cloud module owns those, but we only need to null
# them, which avoids importing telemetry here.
# ----------------------------------------------------------------------

import time

from .. import state, config, runtime, settings as settings_mod
from ..helpers import log

try:
    import wifi
    import socketpool
except ImportError:
    wifi = None
    socketpool = None

try:
    import mdns
except Exception as e:
    mdns = None
    print("mdns IMPORT FAILED:", e)


def stop_mdns():
    if state.mdns_server is not None:
        try:
            state.mdns_server.deinit()
        except Exception:
            pass
        state.mdns_server = None


def start_mdns_if_possible():
    stop_mdns()
    if mdns is None or wifi is None:
        return False
    try:
        if not wifi.radio.connected:  # only meaningful on STA
            return False
    except Exception:
        return False
    try:
        server = mdns.Server(wifi.radio)
        server.hostname = state.mdns_hostname or "knowco2"
        server.advertise_service(service_type="_http", protocol="_tcp", port=80)
        state.mdns_server = server
        print("mDNS started:", server.hostname + ".local")
        return True
    except Exception as e:
        print("mDNS start failed:", e)
        state.mdns_server = None
        return False


def stop_ap():
    if wifi is None:
        return
    try:
        wifi.radio.stop_ap()
    except Exception:
        pass


def disconnect_sta():
    if wifi is None:
        return
    try:
        if wifi.radio.connected:
            wifi.radio.disconnect()
    except Exception:
        pass


def _invalidate_cloud_session():
    # Force a fresh TLS context/session for the new network context.
    state.cloud_session = None
    state.cloud_ctx = None


def switch_to_ap(force_restart=False):
    if wifi is None or socketpool is None:
        runtime.show_status("WiFi not available")
        return False

    _invalidate_cloud_session()
    stop_mdns()
    disconnect_sta()

    s = state.settings
    try:
        settings_mod.ensure_ap_credentials()
        ssid = s.get("ap_ssid", "knowco2")
        password = s.get("ap_password", "")
        if force_restart:
            stop_ap()

        wifi.radio.start_ap(ssid=ssid, password=password)

        ap_ip = None
        for _ in range(50):
            ap_ip = wifi.radio.ipv4_address_ap or wifi.radio.ipv4_address
            if ap_ip is not None:
                break
            time.sleep(0.1)

        if ap_ip is None:
            runtime.show_status("WiFi: no AP IP")
            return False

        state.ip_str_cached = str(ap_ip)
        state.wifi_mode = config.WIFI_MODE_AP
        runtime.show_status("AP: " + state.ip_str_cached)
        print("AP started, IP:", state.ip_str_cached)

        runtime.update_wifi_indicator()

        ok_http = runtime.start_http_server()
        if not ok_http:
            runtime.show_status('HTTP: error')
        if state.screen == config.SCREEN_APINFO:
            runtime.make_or_update_qrs(s.get("ap_ssid", ""), s.get("ap_password", ""), state.ip_str_cached)
            runtime.refresh_apinfo_screen()
        return True

    except Exception as e:
        print("AP start error:", e)
        runtime.show_status("AP error")
        return False


def ensure_sta_connected():
    # Rate-limited: wifi.radio.connect() can block 10-30 s, so only retry
    # after STA_RECONNECT_COOLDOWN_S has elapsed.
    if wifi is None:
        return False
    s = state.settings
    ssid = (s.get("sta_ssid") or "").strip()
    pw = (s.get("sta_password") or "").strip()
    if not ssid or not pw:
        return False

    try:
        if wifi.radio.connected:
            return True
    except Exception:
        pass

    now_mono = time.monotonic()
    if (now_mono - state.last_sta_reconnect_attempt) < config.STA_RECONNECT_COOLDOWN_S:
        return False

    state.last_sta_reconnect_attempt = now_mono

    # Feed watchdog before a potentially long connect().
    if state._wd is not None:
        try:
            state._wd.feed()
        except Exception:
            pass

    try:
        runtime.show_status("WiFi: connecting...")
        wifi.radio.connect(ssid, pw)
        runtime.show_status("WiFi: connected")
        return True
    except Exception as e:
        log("sta", "STA connect failed:", e, min_interval=10.0)
        runtime.show_status("WiFi: connect fail")
        return False


def switch_to_sta():
    if wifi is None or socketpool is None:
        runtime.show_status("WiFi not available")
        return False

    _invalidate_cloud_session()
    state.last_sta_reconnect_attempt = 0.0  # make first reconnect immediate

    stop_ap()
    ok = ensure_sta_connected()
    if not ok:
        return False

    try:
        state.ip_str_cached = str(wifi.radio.ipv4_address)
    except Exception:
        state.ip_str_cached = None

    state.wifi_mode = config.WIFI_MODE_STA
    runtime.show_status("STA: " + (state.ip_str_cached or "ok"))
    print("STA connected, IP:", state.ip_str_cached)

    runtime.update_wifi_indicator()

    ok_http = runtime.start_http_server()
    if not ok_http:
        runtime.show_status('HTTP: error')
    start_mdns_if_possible()

    state.ntp_sync_pending = True  # kick NTP soon after STA comes up

    if state.screen == config.SCREEN_APINFO:
        runtime.refresh_apinfo_screen()
    return True
