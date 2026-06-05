# knowco2/ui/widgets.py
# ----------------------------------------------------------------------
# All displayio objects: the main group, background, status/CO2/temp labels,
# the graph bitmap + palette, axis/threshold labels, AP-info labels, the
# regulatory e-label screen, the TWC status cluster, and the LP/battery
# banners. Created once at import. Other modules reference them as
# `widgets.<name>` (commonly imported as `W`).
#
# Bodies are the original code, moved verbatim; only references to relocated
# names (colour scheme, status duration/timeout) were rewritten.
# ----------------------------------------------------------------------

import time
import displayio
import terminalio
import board
from adafruit_display_text import label

from .. import config, state

display = board.DISPLAY
display.rotation = 180
main_group = displayio.Group()

bg_bitmap = displayio.Bitmap(display.width, display.height, 1)
bg_palette = displayio.Palette(1)
bg_palette[0] = 0x000000
bg = displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette)
main_group.append(bg)

status_label = label.Label(terminalio.FONT, text="", color=0xAAAAAA, scale=1)
status_label.anchor_point = (0.5, 0.0)
status_label.anchored_position = (display.width // 2, 0)
main_group.append(status_label)

# Wi-Fi client indicator (shows when connected in STA mode)
wifi_ind_label = label.Label(terminalio.FONT, text="", color=0x00BCD4, scale=1)
wifi_ind_label.anchor_point = (1.0, 0.0)
wifi_ind_label.anchored_position = (display.width - 2, 2)
main_group.append(wifi_ind_label)

# Cloud indicator (shows when device has successfully posted to cloud)
cloud_ind_label = label.Label(terminalio.FONT, text="", color=0x00BCD4, scale=1)
cloud_ind_label.anchor_point = (1.0, 0.0)
cloud_ind_label.anchored_position = (display.width - 2, 14)
main_group.append(cloud_ind_label)

# ----------------------------------------------------------------------
# Replace text-based WiFi and cloud indicators with custom low-height bitmaps.
#
# To reduce the vertical footprint of the WiFi and cloud status indicators
# without abbreviating their text, we define a tiny 4x5 pixel font and
# compose bitmap-based text for "WIFI", "TWIFI", and "CLOUD".  These
# bitmaps are about five pixels tall rather than the eight pixels of the
# built-in terminal font.  We then create TileGrids for each and
# position them at the top right of the screen.  Their visibility is
# controlled in update_wifi_indicator().

# Define 4x5 pixel patterns for each character required.
_SMALL_FONT_4x5 = {
    "W": [
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 1, 1),
        (1, 1, 0, 1),
        (1, 0, 0, 1),
    ],
    "I": [
        (0, 1, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 1, 1, 0),
    ],
    "F": [
        (1, 1, 1, 1),
        (1, 0, 0, 0),
        (1, 1, 1, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
    ],
    "T": [
        (1, 1, 1, 1),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
        (0, 0, 1, 0),
    ],
    "C": [
        (0, 1, 1, 1),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (0, 1, 1, 1),
    ],
    "L": [
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 0, 0, 0),
        (1, 1, 1, 1),
    ],
    "O": [
        (0, 1, 1, 0),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (0, 1, 1, 0),
    ],
    "U": [
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (0, 1, 1, 0),
    ],
    "D": [
        (1, 1, 1, 0),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 0, 0, 1),
        (1, 1, 1, 0),
    ],
}

def _make_small_text_bitmap(text):
    """Create a small bitmap and palette for the given text using the
    4x5 font defined above. Returns (bitmap, palette, width, height).

    Each character is drawn in a 4x5 grid with a one-pixel spacer
    column between characters.  Palette index 0 is transparent and
    index 1 is the cyan color used by WiFi/cloud indicators."""
    char_width = 4
    char_height = 5
    spacing = 1
    num_chars = len(text)
    width = num_chars * char_width + (num_chars - 1) * spacing
    height = char_height
    bmp = displayio.Bitmap(width, height, 2)
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = 0x00BCD4
    x_off = 0
    for idx, ch in enumerate(text):
        pattern = _SMALL_FONT_4x5.get(ch.upper(), None)
        if pattern:
            for y in range(char_height):
                row = pattern[y]
                for x in range(char_width):
                    if row[x]:
                        bmp[x_off + x, y] = 1
        x_off += char_width
        if idx < num_chars - 1:
            x_off += spacing
    return bmp, pal, width, height

# Build the small-text bitmaps for WiFi and cloud indicators.  We
# construct two WiFi variants: one for plain "WIFI" and another for
# "TWIFI" (prefix T indicates NTP time sync).  We also build a
# cloud text for "CLOUD".  The widths of the bitmaps are used to
# position them flush against the right edge of the display.
# ── TWC status indicator ───────────────────────────────────────────────────
# Three independently-colored single-character labels in the top-right corner.
# T = NTP-synced STA mode  |  W = WiFi connected  |  C = Cloud recently uploaded
# Active color: teal 0x00BCD4.  Inactive: dim 0x333333.
# Laid out right-to-left so they read "T W C" from left to right.
_TWC_ACTIVE = 0x00BCD4
_TWC_DIM    = 0x2A2A2A
_TWC_GAP    = 6   # px per character at scale=1 (terminalio.FONT glyph width)

twc_c_label = label.Label(terminalio.FONT, text="C", color=_TWC_DIM, scale=1)
twc_c_label.anchor_point = (1.0, 0.0)
twc_c_label.anchored_position = (display.width - 2, 2)
main_group.append(twc_c_label)

twc_w_label = label.Label(terminalio.FONT, text="W", color=_TWC_DIM, scale=1)
twc_w_label.anchor_point = (1.0, 0.0)
twc_w_label.anchored_position = (display.width - 2 - _TWC_GAP, 2)
main_group.append(twc_w_label)

twc_t_label = label.Label(terminalio.FONT, text="T", color=_TWC_DIM, scale=1)
twc_t_label.anchor_point = (1.0, 0.0)
twc_t_label.anchored_position = (display.width - 2 - _TWC_GAP * 2, 2)
main_group.append(twc_t_label)


def show_status(msg):
    status_label.text = msg
    state.status_timeout = time.monotonic() + config.STATUS_DURATION

# Persistent "SENSOR ERR" banner — unlike show_status() it stays visible until
# the sensor recovers, ensuring the user always knows when readings have stopped.
sensor_frozen_label = label.Label(terminalio.FONT, text="!! SENSOR ERR", color=0xFF4400, scale=1)
sensor_frozen_label.anchor_point = (0.5, 1.0)
sensor_frozen_label.anchored_position = (display.width // 2, display.height - 2)
sensor_frozen_label.hidden = True
main_group.append(sensor_frozen_label)

# Low-power mode badge — sits immediately left of the TWC indicator cluster.
# TWC uses 3 × 6px chars + 2px right margin = 20px from right edge.
# Add 4px gap → LP right edge at display.width - 24.
lp_badge_label = label.Label(terminalio.FONT, text="LP", color=0x00FF88, scale=1)
lp_badge_label.anchor_point = (1.0, 0.0)
lp_badge_label.anchored_position = (display.width - 2 - _TWC_GAP * 3 - 4, 2)
lp_badge_label.hidden = True
main_group.append(lp_badge_label)

# Low battery warning banner — appears at the bottom of the main screen.
batt_warn_label = label.Label(terminalio.FONT, text="!! LOW BATT", color=0xFF8800, scale=1)
batt_warn_label.anchor_point = (0.5, 1.0)
batt_warn_label.anchored_position = (display.width // 2, display.height - 12)
batt_warn_label.hidden = True
main_group.append(batt_warn_label)

co2_label = label.Label(terminalio.FONT, text="CO2: ---- ppm", color=0x00FF00, scale=3)
co2_label.anchor_point = (0.5, 0.5)
co2_label.anchored_position = (display.width // 2, display.height // 2 - 20)
main_group.append(co2_label)

# Separate "ppm" label used only in Big CO2 mode so the number can be
# scaled as large as possible without the "ppm" suffix eating screen space.
ppm_label = label.Label(terminalio.FONT, text="ppm", color=0x00FF00, scale=3)
ppm_label.anchor_point = (0.5, 0.0)
ppm_label.anchored_position = (display.width // 2, display.height - 28)
ppm_label.hidden = True
main_group.append(ppm_label)

th_label = label.Label(terminalio.FONT, text="--.-F  --.-%", color=0xFFFFFF, scale=2)
th_label.anchor_point = (0.5, 0.5)
th_label.anchored_position = (display.width // 2, display.height // 2 + 22)
main_group.append(th_label)

GRAPH_Y = 20
GRAPH_HEIGHT = display.height - GRAPH_Y
GRAPH_MARGIN = 30                           # px reserved on the left for Y-axis labels
GRAPH_WIDTH = display.width - GRAPH_MARGIN  # graph bitmap width (210 px)

graph_bitmap = displayio.Bitmap(GRAPH_WIDTH, GRAPH_HEIGHT, 7)
graph_palette = displayio.Palette(7)
graph_palette[0] = 0x000000
graph_palette[1] = 0x202020
graph_palette[2] = config.SCHEME_NORMAL["low"]
graph_palette[3] = config.SCHEME_NORMAL["med"]
graph_palette[4] = config.SCHEME_NORMAL["alert"]
graph_palette[5] = 0xFFFFFF  # latest-point dot
graph_palette[6] = 0x666666  # Y-axis and X-axis border lines

graph = displayio.TileGrid(graph_bitmap, pixel_shader=graph_palette,
                            x=GRAPH_MARGIN, y=GRAPH_Y)
main_group.append(graph)

y_min_label = label.Label(terminalio.FONT, text="-5.0m", color=0x00B4D8, scale=1)
y_min_label.anchor_point = (0.0, 1.0)
y_min_label.anchored_position = (2, GRAPH_Y + GRAPH_HEIGHT - 1)
main_group.append(y_min_label)

y_max_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
y_max_label.anchor_point = (0.0, 0.0)
y_max_label.anchored_position = (2, GRAPH_Y)
main_group.append(y_max_label)

# Static Y-axis label at top-left — replaces the old "t-5.0m" span label.
# Tells the user that the left axis is CO₂ in ppm.
x_left_label = label.Label(terminalio.FONT, text="CO2 ppm", color=0x888888, scale=1)
x_left_label.anchor_point = (0.0, 0.0)
x_left_label.anchored_position = (2, 2)
main_group.append(x_left_label)

x_right_label = label.Label(terminalio.FONT, text="now", color=0x00B4D8, scale=1)
x_right_label.anchor_point = (1.0, 1.0)
x_right_label.anchored_position = (display.width - 1, GRAPH_Y + GRAPH_HEIGHT - 1)
main_group.append(x_right_label)

# Midpoint time label — static "-2.5m" since the window is always 5 minutes.
x_mid_label = label.Label(terminalio.FONT, text="-2.5m", color=0x00B4D8, scale=1)
x_mid_label.anchor_point = (0.5, 1.0)
x_mid_label.anchored_position = (GRAPH_MARGIN + GRAPH_WIDTH // 2, GRAPH_Y + GRAPH_HEIGHT - 1)
x_mid_label.hidden = True
main_group.append(x_mid_label)

low_label = label.Label(terminalio.FONT, text="LOW", color=config.SCHEME_NORMAL["low"], scale=1)
low_label.anchor_point = (0.0, 0.5)
low_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.80))
main_group.append(low_label)

med_label = label.Label(terminalio.FONT, text="MED", color=config.SCHEME_NORMAL["med"], scale=1)
med_label.anchor_point = (0.0, 0.5)
med_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.50))
main_group.append(med_label)

high_label = label.Label(terminalio.FONT, text="HIGH", color=config.SCHEME_NORMAL["alert"], scale=1)
high_label.anchor_point = (0.0, 0.5)
high_label.anchored_position = (2, GRAPH_Y + int(GRAPH_HEIGHT * 0.20))
main_group.append(high_label)

graph_value_label = label.Label(terminalio.FONT, text="", color=0xFFFFFF, scale=1)
graph_value_label.anchor_point = (0.5, 0.0)
graph_value_label.anchored_position = (display.width // 2, 10)
main_group.append(graph_value_label)

# AP Info screen (also shows STA + mDNS if connected)
ap_ssid_label = label.Label(terminalio.FONT, text="", color=0xFFFFFF, scale=1)
ap_ssid_label.anchor_point = (0.0, 0.0)
ap_ssid_label.anchored_position = (6, 10)
main_group.append(ap_ssid_label)

ap_pass_label = label.Label(terminalio.FONT, text="", color=0x00BCD4, scale=1)
ap_pass_label.anchor_point = (0.0, 0.0)
ap_pass_label.anchored_position = (6, 28)
main_group.append(ap_pass_label)

ap_ip_label = label.Label(terminalio.FONT, text="", color=0xFFFFFF, scale=1)
ap_ip_label.anchor_point = (0.0, 0.0)
ap_ip_label.anchored_position = (6, 58)
main_group.append(ap_ip_label)

ap_batt_label = label.Label(terminalio.FONT, text="", color=0xAAAAAA, scale=1)
ap_batt_label.anchor_point = (0.0, 0.0)
ap_batt_label.anchored_position = (6, 74)
main_group.append(ap_batt_label)

ap_hw_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
ap_hw_label.anchor_point = (0.0, 0.0)
ap_hw_label.anchored_position = (6, 88)
main_group.append(ap_hw_label)

ap_scd_label = label.Label(terminalio.FONT, text="", color=0x888888, scale=1)
ap_scd_label.anchor_point = (0.0, 0.0)
ap_scd_label.anchored_position = (6, 102)
main_group.append(ap_scd_label)

ap_fw_label = label.Label(terminalio.FONT, text="", color=0x666666, scale=1)
ap_fw_label.anchor_point = (0.0, 0.0)
ap_fw_label.anchored_position = (6, 116)
main_group.append(ap_fw_label)

# --- Regulatory / FCC e-label screen (SCREEN_REGULATORY) ---
# Shown when user holds B for 2s while on the info screen (SCREEN_APINFO).
# Satisfies FCC 47 CFR §2.935 electronic labeling requirement.
# Access path: Info screen → hold B 2s  (≤2 steps from settings, within the 3-step rule)
reg_title_label = label.Label(terminalio.FONT, text="-- REGULATORY INFO --",
                               color=0x666666, scale=1)
reg_title_label.anchor_point = (0.0, 0.0)
reg_title_label.anchored_position = (6, 6)
reg_title_label.hidden = True
main_group.append(reg_title_label)

reg_fcc_label = label.Label(terminalio.FONT, text="FCC ID: 2AC7Z-ESPS3MINI1",
                             color=0xFFFFFF, scale=1)
reg_fcc_label.anchor_point = (0.0, 0.0)
reg_fcc_label.anchored_position = (6, 22)
reg_fcc_label.hidden = True
main_group.append(reg_fcc_label)

reg_module_label = label.Label(terminalio.FONT, text="Contains cert'd module",
                                color=0xAAAAAA, scale=1)
reg_module_label.anchor_point = (0.0, 0.0)
reg_module_label.anchored_position = (6, 36)
reg_module_label.hidden = True
main_group.append(reg_module_label)

reg_part15_label = label.Label(terminalio.FONT, text="FCC Part 15 compliant",
                                color=0xFFFFFF, scale=1)
reg_part15_label.anchor_point = (0.0, 0.0)
reg_part15_label.anchored_position = (6, 50)
reg_part15_label.hidden = True
main_group.append(reg_part15_label)

reg_rohs_label = label.Label(terminalio.FONT, text="RoHS Compliant",
                              color=0x00D68F, scale=1)
reg_rohs_label.anchor_point = (0.0, 0.0)
reg_rohs_label.anchored_position = (6, 64)
reg_rohs_label.hidden = True
main_group.append(reg_rohs_label)

reg_company_label = label.Label(terminalio.FONT, text="KNOWCO2 LLC",
                                 color=0xFFFFFF, scale=1)
reg_company_label.anchor_point = (0.0, 0.0)
reg_company_label.anchored_position = (6, 78)
reg_company_label.hidden = True
main_group.append(reg_company_label)

reg_url_label = label.Label(terminalio.FONT, text="knowco2.com/compliance",
                             color=0x00B4D8, scale=1)
reg_url_label.anchor_point = (0.0, 0.0)
reg_url_label.anchored_position = (6, 92)
reg_url_label.hidden = True
main_group.append(reg_url_label)

reg_hint_label = label.Label(terminalio.FONT, text="release B to go back",
                              color=0x444444, scale=1)
reg_hint_label.anchor_point = (0.0, 0.0)
reg_hint_label.anchored_position = (6, 118)
reg_hint_label.hidden = True
main_group.append(reg_hint_label)

_REG_LABELS = [reg_title_label, reg_fcc_label, reg_module_label,
               reg_part15_label, reg_rohs_label, reg_company_label,
               reg_url_label, reg_hint_label]

display.root_group = main_group
