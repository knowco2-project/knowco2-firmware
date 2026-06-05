# knowco2/ui/screens.py
# ----------------------------------------------------------------------
# UI logic: colour scheme, alert colours, trend arrow, text refresh, QR code
# build, AP-info screen, Wi-Fi/cloud indicator, visibility, and the graph
# renderer. These read shared state and draw onto the widgets.
#
# Bodies are the original code, moved verbatim; identifier references were
# rewritten at the token level to point at the package (state.*, config.*,
# version.*, widgets as W.*). Logic, strings, and comments are unchanged.
# ----------------------------------------------------------------------

import time
import gc

from .. import state, config, version, runtime, ids
from . import widgets as W
from ..helpers import log

try:
    import wifi
except ImportError:
    wifi = None

try:
    import displayio
    import terminalio
    from adafruit_display_text import label
except Exception:
    displayio = None
    terminalio = None

try:
    import adafruit_miniqr
except Exception as e:
    adafruit_miniqr = None
    print("miniqr IMPORT FAILED:", e)

def color_for_co2(co2):
    if co2 < state.LOW_THRESHOLD:
        return state.active_scheme["low"]
    elif co2 < state.MED_THRESHOLD:
        return state.active_scheme["med"]
    else:
        return state.active_scheme["alert"]

def graph_color_index_for_co2(val):
    if val < state.LOW_THRESHOLD:
        return 2
    elif val < state.MED_THRESHOLD:
        return 3
    else:
        return 4

def apply_color_scheme():
    """Apply the active color scheme to all live display elements.

    Call this after changing settings["colorblind_mode"] and whenever
    the device first starts.  Safe to call at any time.
    """
    cb = state.settings.get("colorblind_mode", False)
    state.active_scheme = config.SCHEME_CB if cb else config.SCHEME_NORMAL
    # Update the graph bitmap palette (affects all bars immediately on refresh)
    W.graph_palette[2] = state.active_scheme["low"]
    W.graph_palette[3] = state.active_scheme["med"]
    W.graph_palette[4] = state.active_scheme["alert"]
    # Update threshold line label colors
    try:
        W.low_label.color  = state.active_scheme["low"]
        W.med_label.color  = state.active_scheme["med"]
        W.high_label.color = state.active_scheme["alert"]
    except Exception:
        pass
    # Update graph time-axis label colors.
    # Normal mode: brand teal contrasts well against yellow bars.
    # Colorblind mode: white — no conflict with the blue/amber/vermillion palette.
    _axis_label_color = 0xFFFFFF if cb else 0x00B4D8
    try:
        W.x_right_label.color = _axis_label_color
        W.x_mid_label.color   = _axis_label_color
    except Exception:
        pass
    # Refresh the live CO2 display color immediately
    try:
        if state.last_co2 is not None:
            apply_alert_colors(state.last_co2)
    except Exception:
        pass

def apply_alert_colors(co2):
    if not state.alerts_enabled:
        W.co2_label.color = 0xFFFFFF
        W.graph_value_label.color = 0xFFFFFF
        return
    c = color_for_co2(co2)
    W.co2_label.color = c
    W.graph_value_label.color = c

def compute_trend_arrow():
    if state.last_co2 is None:
        return "-"

    try:
        lookback_samples = int(config.TREND_LOOKBACK_SECONDS / config.SCD_MEASUREMENT_PERIOD)
    except Exception:
        lookback_samples = 0

    prev = None
    if lookback_samples >= 1 and len(state.co2_history) > lookback_samples:
        prev = state.co2_history[-(lookback_samples + 1)]
    elif state.last_co2_prev is not None:
        prev = state.last_co2_prev

    if prev is None:
        return "-"

    diff = state.last_co2 - prev
    if diff > config.TREND_DEADBAND:
        return "↑"
    elif diff < -config.TREND_DEADBAND:
        return "↓"
    else:
        return "→"

def refresh_text():

    if state.screen != config.SCREEN_MAIN:
        return

    if state.display_mode == 2:
        # Show the most recent CO₂ value along with the trend arrow and
        # the instantaneous rate of change (ppm per second) if available.
        if state.last_co2 is not None:
            arrow = compute_trend_arrow()
            if state.rate_of_change is not None:
                # Show a sign (+/-) and one decimal place for the rate.  Use ppm/s units.
                W.graph_value_label.text = "%d ppm %s %+.1f ppm/s%s" % (int(state.last_co2), arrow, state.rate_of_change, " [LP]" if state.energy_mode else "")
            else:
                W.graph_value_label.text = "%d ppm %s%s" % (int(state.last_co2), arrow, " [LP]" if state.energy_mode else "")
        else:
            W.graph_value_label.text = "-- ppm"
    else:
        # When not in graph-only mode, hide the graph value label text.
        W.graph_value_label.text = ""

    # CO2 + temperature are required to draw a reading; humidity is optional
    # (CO2-only sensors such as the Senseair Sunrise report rh = None).
    if state.last_co2 is None or state.last_temp_c is None:
        if state.display_mode in (0, 1):
            W.co2_label.text = "CO2: ---- ppm"
            W.co2_label.scale = 3
            W.ppm_label.hidden = True
            if state.display_mode == 0:
                W.th_label.text = "--.-F  --.-%"
        return

    co2 = state.last_co2
    t_c = state.last_temp_c
    rh = state.last_rh
    t_f = t_c * 9 / 5 + 32

    if state.display_mode == 1:
        # Big CO2 mode: number only, no units, maximum readable scale.
        # terminalio.FONT is 6 px wide per character at scale=1.
        # Scales chosen so the widest expected value (9999 = 4 chars) still fits
        # the 240 px display with a small margin, and 5 digits (10000) still fits.
        co2_str = "%d" % int(co2)
        ndigits = len(co2_str)
        if ndigits <= 2:
            big_scale = 16   # 2 chars × 6 × 16 = 192 px wide
        elif ndigits == 3:
            big_scale = 12   # 3 chars × 6 × 12 = 216 px wide
        elif ndigits == 4:
            big_scale = 9    # 4 chars × 6 × 9  = 216 px wide
        else:
            big_scale = 7    # 5 chars × 6 × 7  = 210 px wide
        W.co2_label.scale = big_scale
        W.co2_label.text = co2_str
        W.co2_label.anchored_position = (W.display.width // 2, W.display.height // 2)
        W.ppm_label.hidden = True
    elif state.display_mode == 0:
        W.co2_label.text = "CO2: %d ppm" % int(co2)

    if state.display_mode == 0:
        rh_str = ("%.1f%%" % rh) if rh is not None else "--%"
        if state.temp_mode == "F":
            W.th_label.text = "%.1fF  %s" % (t_f, rh_str)
        else:
            W.th_label.text = "%.1fC  %s" % (t_c, rh_str)

def build_wifi_qr_payload(ssid, pw):
    return "WIFI:T:WPA;S:%s;P:%s;;" % (ssid, pw)

def build_url_qr_payload(ip_str):
    return "http://%s/" % ip_str

def _make_qr_tile(payload, x0, y0, scale=2, target_modules=None):
    if adafruit_miniqr is None:
        return None

    qr = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
    qr.add_data(payload)
    qr.make()

    m = qr.matrix
    modules = m.width

    if target_modules is None:
        target_modules = modules
    if target_modules < modules:
        target_modules = modules

    size = target_modules * scale
    bmp = displayio.Bitmap(size, size, 2)
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = 0xFFFFFF

    off = ((target_modules - modules) // 2) * scale

    for y in range(modules):
        for x in range(modules):
            if m[x, y]:
                ox = off + x * scale
                oy = off + y * scale
                for yy in range(scale):
                    for xx in range(scale):
                        bmp[ox + xx, oy + yy] = 1

    return displayio.TileGrid(bmp, pixel_shader=pal, x=x0, y=y0)

def make_or_update_qrs(ssid, pw, ip_str):
    """Create/refresh the QR code(s) on the AP info screen.

    In STA mode: always shows the URL QR (single page, full size).
    In AP mode: shows one QR at a time, toggled by _qr_page:
      page 0 -> WiFi join QR  ("Scan to join"  +  "1/2")
      page 1 -> URL / IP QR   ("Open page"     +  "2/2")

    Rebuild only when payload or page changes to avoid flicker.
    """

    if adafruit_miniqr is None:
        return

    try:
        # Prefer mDNS URL when in STA (friendly + stable), else use IP.
        if state.wifi_mode == config.WIFI_MODE_STA and state.mdns_hostname:
            url_payload = "http://%s.local/" % state.mdns_hostname
        else:
            url_payload = build_url_qr_payload(ip_str)

        wifi_payload = build_wifi_qr_payload(ssid, pw)

        # If nothing changed (including page), do nothing.
        if (wifi_payload == state._last_wifi_payload and url_payload == state._last_url_payload
                and state._qr_page == state._last_qr_page):
            return

        # Remove old QR objects (if any)
        for obj in (state.qr_tilegrid_wifi, state.qr_tilegrid_url, state.qr_caption1, state.qr_caption2, state.qr_page_indicator):
            if obj is not None:
                try:
                    W.main_group.remove(obj)
                except Exception:
                    pass

        state.qr_tilegrid_wifi = None
        state.qr_tilegrid_url = None
        state.qr_caption1 = None
        state.qr_caption2 = None
        state.qr_page_indicator = None

        margin = 2
        avail_h = W.display.height - (2 * margin)
        caption_h = 12  # terminalio font cell height
        cap_pad = 2     # gap between caption bottom and QR top

        # Measure module counts for both QRs.
        tmp = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
        tmp.add_data(wifi_payload); tmp.make()
        modules_wifi = tmp.matrix.width

        tmp2 = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
        tmp2.add_data(url_payload); tmp2.make()
        modules_url = tmp2.matrix.width

        # Choose which QR to show and what caption to use.
        if state.wifi_mode == config.WIFI_MODE_STA:
            # STA: always show URL QR, no page indicator needed.
            target_modules = modules_url
            payload = url_payload
            caption1_text = "Open page"
            show_indicator = False
        else:
            # AP: page-toggle between WiFi QR (page 0) and URL QR (page 1).
            if state._qr_page == 0:
                target_modules = modules_wifi
                payload = wifi_payload
                caption1_text = "Scan to join"
            else:
                target_modules = modules_url
                payload = url_payload
                caption1_text = "Open page"
            show_indicator = True

        scale = 2
        for _s in (4, 3, 2):
            if (caption_h + cap_pad + target_modules * _s) <= avail_h:
                scale = _s
                break
        size = target_modules * scale
        top_y = margin + max(0, (avail_h - caption_h - cap_pad - size) // 2)
        cap1_y = top_y
        qr1_y  = top_y + caption_h + cap_pad

        # Right-align the QR block.
        cap_w = len(caption1_text) * 6
        right_x = W.display.width - margin - max(size, cap_w) - margin
        if right_x < 2:
            right_x = 2

        # Build the TileGrid.
        if state.wifi_mode == config.WIFI_MODE_STA or state._qr_page == 1:
            state.qr_tilegrid_url = _make_qr_tile(payload, right_x, qr1_y,
                                            scale=scale, target_modules=target_modules)
            if state.qr_tilegrid_url is not None:
                W.main_group.append(state.qr_tilegrid_url)
        else:
            state.qr_tilegrid_wifi = _make_qr_tile(payload, right_x, qr1_y,
                                             scale=scale, target_modules=target_modules)
            if state.qr_tilegrid_wifi is not None:
                W.main_group.append(state.qr_tilegrid_wifi)

        state.qr_caption1 = label.Label(terminalio.FONT, text=caption1_text, color=0xAAAAAA, scale=1)
        state.qr_caption1.anchor_point = (0.0, 0.0)
        state.qr_caption1.anchored_position = (right_x, cap1_y)
        W.main_group.append(state.qr_caption1)

        # Page indicator "1/2" or "2/2" bottom-right, dimmed.
        if show_indicator:
            ind_text = "1/2" if state._qr_page == 0 else "2/2"
            state.qr_page_indicator = label.Label(terminalio.FONT, text=ind_text, color=0x555555, scale=1)
            state.qr_page_indicator.anchor_point = (1.0, 1.0)
            state.qr_page_indicator.anchored_position = (W.display.width - margin, W.display.height - margin)
            W.main_group.append(state.qr_page_indicator)

        state._last_wifi_payload = wifi_payload
        state._last_url_payload = url_payload
        state._last_qr_target_modules = target_modules
        state._last_qr_scale = scale
        state._last_qr_right_x = right_x
        state._last_qr_page = state._qr_page
    except Exception as e:
        state._last_wifi_payload = None
        state._last_url_payload = None
        state._last_qr_page = None
        log("qr", "QR update failed:", e, min_interval=2.0)

def refresh_apinfo_screen():
    ssid = state.settings.get("ap_ssid", "")
    pw = state.settings.get("ap_password", "")
    ip = state.ip_str_cached or "--.--.--.--"

    # Make AP password easier to read *before* the device joins a network.
    # In STA mode we keep the .local line at the normal size.
    try:
        if state.wifi_mode == config.WIFI_MODE_AP:
            W.ap_pass_label.scale = 2
            W.ap_pass_label.anchored_position = (6, 24)
        else:
            W.ap_pass_label.scale = 1
            W.ap_pass_label.anchored_position = (6, 28)
    except Exception:
        pass

    # Show different headline depending on mode
    if state.wifi_mode == config.WIFI_MODE_STA:
        W.ap_ssid_label.text = "STA: " + (state.settings.get("sta_ssid", "") or "")
        W.ap_pass_label.text = ids.friendly_mdns_label(state.mdns_hostname) or "(mdns off)"
        W.ap_ip_label.text = "IP:  " + ip
    else:
        W.ap_ssid_label.text = "SSID: " + ssid
        W.ap_pass_label.text = pw
        W.ap_ip_label.text = "IP:   " + ip

    vbat, pct = state.cached_vbat, state.cached_pct
    if vbat is None:
        W.ap_batt_label.text = "Battery: N/A"
    else:
        W.ap_batt_label.text = "Battery: %.2fV (%d%%)" % (vbat, pct)

    hw = state.hwid_hex or "N/A"
    hw_short = (hw[:12] + "…") if (hw and len(hw) > 12) else hw

    scd_sn = state.scd_serial_str or "N/A"
    scd_short = (scd_sn[:12] + "…") if (scd_sn and len(scd_sn) > 12) else scd_sn

    W.ap_hw_label.text = "HW:  " + (hw_short or "N/A")
    W.ap_scd_label.text = state.sensor_model_str + ": " + (scd_short or "N/A")
    W.ap_fw_label.text = "FW:" + version.FIRMWARE_VERSION + "  CP:" + version.CP_VERSION

    # Keep QR codes in sync with the current mode / address.
    if state.screen == config.SCREEN_APINFO and adafruit_miniqr is not None:
        try:
            if state.wifi_mode == config.WIFI_MODE_AP:
                make_or_update_qrs(state.settings.get("ap_ssid", ""), state.settings.get("ap_password", ""), state.ip_str_cached or "192.168.4.1")
            else:
                # In STA, URL QR will prefer mDNS automatically inside make_or_update_qrs().
                make_or_update_qrs(state.settings.get("ap_ssid", ""), state.settings.get("ap_password", ""), state.ip_str_cached or "0.0.0.0")
        except Exception as _e:
            pass


def update_wifi_indicator():
    """Update the TWC top-right status indicator letters.

    T = NTP-synced STA connection (teal when active, dim otherwise)
    W = WiFi connected in STA mode (teal when active, dim otherwise)
    C = Cloud upload succeeded recently (teal when active, dim otherwise)

    In AP mode while on the info/QR screen the QR captions start near y=2,
    so all three letters are dimmed to avoid overlap.
    """
    try:
        sta_connected = (wifi is not None and state.wifi_mode == config.WIFI_MODE_STA
                         and wifi.radio.connected)
    except Exception:
        sta_connected = False

    try:
        _now = time.monotonic()
        _cloud_ok_recent = (state.cloud_last_ok > 0.0) and ((_now - state.cloud_last_ok) <= config.CLOUD_OK_TTL)
        cloud_active = state.cloud_enabled and state.wifi_mode == config.WIFI_MODE_STA and _cloud_ok_recent
    except Exception:
        cloud_active = False

    # Dim all letters when on AP-mode info screen to avoid QR caption overlap.
    if state.screen == config.SCREEN_APINFO and state.wifi_mode == config.WIFI_MODE_AP:
        W.twc_t_label.color = W._TWC_DIM
        W.twc_w_label.color = W._TWC_DIM
        W.twc_c_label.color = W._TWC_DIM
        return

    W.twc_t_label.color = W._TWC_ACTIVE if (sta_connected and state.ntp_synced) else W._TWC_DIM
    W.twc_w_label.color = W._TWC_ACTIVE if sta_connected else W._TWC_DIM
    W.twc_c_label.color = W._TWC_ACTIVE if cloud_active else W._TWC_DIM

    # Keep legacy placeholder labels silent.
    try:
        W.wifi_ind_label.text  = ""
        W.cloud_ind_label.text = ""
    except Exception:
        pass
def update_visibility():
    main_visible = (state.screen == config.SCREEN_MAIN)
    ap_visible = (state.screen == config.SCREEN_APINFO)

    # Sensor frozen banner only appears on the main screen.
    W.sensor_frozen_label.hidden = not (main_visible and state.sensor_frozen_shown)

    # LP badge shown on all screens except graph mode (where [LP] is appended
    # to graph_value_label text instead). New top-right position clears all
    # other labels on both SCREEN_MAIN and SCREEN_APINFO.
    try:
        W.lp_badge_label.hidden = not state.energy_mode or show_graph
    except Exception:
        pass
    # Battery warning only on main screen (managed in batt-refresh block too).
    try:
        _bv = (state.fuel_gauge is not None and state.cached_pct is not None
               and state.cached_pct < config.BATT_WARN_PCT)
        W.batt_warn_label.hidden = not (main_visible and _bv)
    except Exception:
        pass

    W.th_label.hidden = not main_visible

    show_graph = main_visible and (state.display_mode == 2)
    W.graph.hidden = not show_graph
    W.y_min_label.hidden = not show_graph
    W.y_max_label.hidden = not show_graph
    W.x_left_label.hidden = not show_graph
    W.x_right_label.hidden = not show_graph
    W.x_mid_label.hidden = not show_graph
    W.low_label.hidden = not show_graph
    W.med_label.hidden = not show_graph
    W.high_label.hidden = not show_graph
    W.graph_value_label.hidden = not show_graph

    # In graph mode the co2_label must be explicitly hidden — it is still
    # "main_visible" so the generic hide above does not catch it, and a
    # large scale (e.g. 12) left over from big-CO2 mode would bleed into
    # the graph area as a ghost line.
    W.co2_label.hidden = not main_visible or (state.display_mode == 2)

    if main_visible:
        if state.display_mode == 0:
            W.co2_label.scale = 3
            # Restore the position that big-CO2 mode may have overwritten.
            W.co2_label.anchored_position = (W.display.width // 2, W.display.height // 2 - 22)
            W.th_label.hidden = False
        elif state.display_mode == 1:
            # Scale and position are set dynamically in refresh_text().
            W.th_label.hidden = True
    W.ppm_label.hidden = True  # never shown; kept in group for future use

    W.ap_ssid_label.hidden = not ap_visible
    W.ap_pass_label.hidden = not ap_visible
    W.ap_ip_label.hidden = not ap_visible
    W.ap_batt_label.hidden = not ap_visible
    W.ap_hw_label.hidden = not ap_visible
    W.ap_scd_label.hidden = not ap_visible
    W.ap_fw_label.hidden = not ap_visible

    for _obj in (state.qr_tilegrid_wifi, state.qr_tilegrid_url, state.qr_caption1, state.qr_caption2, state.qr_page_indicator):
        if _obj is not None:
            _obj.hidden = not ap_visible

    # Regulatory screen labels — only visible on SCREEN_REGULATORY.
    # Also ensure APINFO labels are hidden when regulatory screen is active.
    reg_visible = (state.screen == config.SCREEN_REGULATORY)
    for _rl in W._REG_LABELS:
        _rl.hidden = not reg_visible
    if reg_visible:
        W.ap_ssid_label.hidden = True
        W.ap_pass_label.hidden = True
        W.ap_ip_label.hidden = True
        W.ap_batt_label.hidden = True
        W.ap_hw_label.hidden = True
        W.ap_scd_label.hidden = True
        W.ap_fw_label.hidden = True
        for _obj in (state.qr_tilegrid_wifi, state.qr_tilegrid_url, state.qr_caption1, state.qr_caption2, state.qr_page_indicator):
            if _obj is not None:
                _obj.hidden = True


def update_axis_labels(low, high, span_seconds):
    # Y-axis max scale value (top of graph) — the only dynamic label now.
    W.y_max_label.text = str(int(high))
    # All other axis labels are static:
    #   y_min_label  = "t-5.0m"  (bottom-left  X-axis anchor)
    #   x_mid_label  = "-2.5m"   (bottom-centre X-axis midpoint)
    #   x_right_label= "now"     (bottom-right  X-axis anchor)
    #   x_left_label = "CO2 ppm" (top-left      Y-axis label)
    # Nothing to update for those.

def _graph_y_for_value(val, low, high):
    span = max(1, high - low)
    v = max(low, min(val, high))
    frac = (v - low) / span
    h = int(frac * (W.GRAPH_HEIGHT - 1))
    return W.GRAPH_HEIGHT - 1 - h

def _set_threshold_label_positions(low, high):
    y_low = _graph_y_for_value(state.LOW_THRESHOLD, low, high)
    y_med = _graph_y_for_value(state.MED_THRESHOLD, low, high)
    y_alert = _graph_y_for_value(state.ALERT_THRESHOLD, low, high)

    W.low_label.anchored_position = (2, W.GRAPH_Y + y_low)
    W.med_label.anchored_position = (2, W.GRAPH_Y + y_med)
    W.high_label.anchored_position = (2, W.GRAPH_Y + y_alert)

    W.low_label.text = str(int(state.LOW_THRESHOLD))
    W.med_label.text = str(int(state.MED_THRESHOLD))
    W.high_label.text = str(int(state.ALERT_THRESHOLD))

def redraw_graph():
    # If a redraw is already underway, skip this call to avoid stalling the UI.
    if state.graph_drawing:
        return
    state.graph_drawing = True
    try:
        W.graph_bitmap.fill(0)
        # If there is no CO2 history yet, just clear the axis labels.
        if not state.co2_history:
            W.x_left_label.text = ""
            W.x_right_label.text = ""
        else:
            # Build a view into the most recent data points.
            n_total = len(state.co2_history)
            start_index = max(0, n_total - config.WINDOW_SAMPLES)
            visible = state.co2_history[start_index:]
            n = len(visible)

            span_seconds = min(config.WINDOW_SECONDS, max(0, (n - 1) * config.SCD_MEASUREMENT_PERIOD))

            # Determine auto-scaling for the graph.
            if state.graph_scale_mode == "fixed":
                low, high = 400, 2000
            elif state.graph_scale_mode == "wide":
                low, high = 400, 3000
            else:
                low = max(400, min(visible))
                high = max(800, max(visible))

            span = max(1, high - low)

            # Divide by (WINDOW_SAMPLES-1) so a full buffer of 61 samples
            # at 4 px each = 240 px, filling the display edge-to-edge.
            # Proportional bar placement: oldest bar starts at x=2 (just right of
            # the Y-axis line), newest bar ends at GRAPH_WIDTH-1 (right edge).
            # This guarantees no gap at the left regardless of PIXELS_PER_SAMPLE.
            _total_px = W.GRAPH_WIDTH - 2  # drawable columns excluding Y-axis line

            # Horizontal grid lines at 25 %, 50 % and 75 % of graph height.
            for y in [int(W.GRAPH_HEIGHT * 0.25), int(W.GRAPH_HEIGHT * 0.5), int(W.GRAPH_HEIGHT * 0.75)]:
                if 0 <= y < W.GRAPH_HEIGHT:
                    for x in range(W.GRAPH_WIDTH):
                        W.graph_bitmap[x, y] = 1

            # Vertical grid lines every 20 px from the Y-axis.
            for x in range(2, W.GRAPH_WIDTH, 20):
                for yy in range(W.GRAPH_HEIGHT):
                    if W.graph_bitmap[x, yy] == 0:
                        W.graph_bitmap[x, yy] = 1

            latest_x = W.GRAPH_WIDTH - 1
            latest_y = None

            for k in range(n):
                if k % 10 == 0:
                    runtime.poll_buttons()
                val = max(low, min(visible[k], high))
                frac = (val - low) / span
                h = int(frac * (W.GRAPH_HEIGHT - 1))
                color_idx = graph_color_index_for_co2(val)

                # Map sample index to pixel column proportionally.
                x_start = 2 + int(k * _total_px / max(n, 1))
                x_end   = min(2 + int((k + 1) * _total_px / max(n, 1)) - 1,
                              W.GRAPH_WIDTH - 1)
                if x_start > x_end:
                    x_end = x_start

                for x in range(x_start, x_end + 1):
                    for yy in range(W.GRAPH_HEIGHT - 1, W.GRAPH_HEIGHT - 1 - h, -1):
                        W.graph_bitmap[x, yy] = color_idx

                if k == n - 1:
                    latest_x = x_end
                    latest_y = W.GRAPH_HEIGHT - 1 - h

            # White dot on the most recent point.
            if latest_y is not None:
                for dy in (-1, 0, 1):
                    yy = latest_y + dy
                    if 0 <= yy < W.GRAPH_HEIGHT:
                        W.graph_bitmap[latest_x, yy] = 5

            # "now" and midpoint labels have fixed positions — no repositioning needed.

            # Update the threshold labels and Y-axis scale label.
            _set_threshold_label_positions(low, high)
            update_axis_labels(low, high, span_seconds)

            # ── Axis border lines (drawn last so they sit over all bars) ──
            # Y-axis: 2-pixel wide vertical line at the left edge of the graph bitmap.
            # On screen this appears at x = GRAPH_MARGIN, forming a clear border
            # between the label gutter and the plotted area.
            for yy in range(W.GRAPH_HEIGHT):
                W.graph_bitmap[0, yy] = 6
                if W.GRAPH_WIDTH > 1:
                    W.graph_bitmap[1, yy] = 6
            # X-axis: 2-pixel tall horizontal line at the very bottom of the bitmap.
            for xx in range(W.GRAPH_WIDTH):
                W.graph_bitmap[xx, W.GRAPH_HEIGHT - 1] = 6
                if W.GRAPH_HEIGHT > 1:
                    W.graph_bitmap[xx, W.GRAPH_HEIGHT - 2] = 6
    finally:
        # Mark redraw complete so future redraw requests may proceed
        state.graph_drawing = False
