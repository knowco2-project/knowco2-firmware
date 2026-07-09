#!/usr/bin/env python3
"""RC-50 SnappyUI tests: QR generation count, cache behavior, deferred
rebuild flag flow, pending-press capture, and label write guarding."""
import sys, types, time as _t

sys.path.insert(0, "..")

calls = {"qr_makes": 0, "polls": 0, "label_sets": 0}

# ---- mock displayio / terminalio / miniqr ------------------------------
dio = types.ModuleType("displayio")
class Bitmap:
    def __init__(self, w, h, n): self.w, self.h = w, h
    def __setitem__(self, k, v): pass
    def fill(self, v): pass
class Palette:
    def __init__(self, n): self._c = [0]*n
    def __setitem__(self, k, v): self._c[k] = v
class TileGrid:
    def __init__(self, bmp, pixel_shader=None, x=0, y=0): pass
class Group(list):
    def append(self, o): super().append(o)
    def remove(self, o):
        if o in self: super().remove(o)
dio.Bitmap, dio.Palette, dio.TileGrid, dio.Group = Bitmap, Palette, TileGrid, Group
sys.modules["displayio"] = dio

term = types.ModuleType("terminalio"); term.FONT = object()
sys.modules["terminalio"] = term

adt = types.ModuleType("adafruit_display_text")
class Label:
    def __init__(self, font, text="", color=0, scale=1, **k):
        self._text = text
        self.anchor_point = (0,0); self.anchored_position = (0,0)
        self.hidden = False; self.scale = scale
    @property
    def text(self): return self._text
    @text.setter
    def text(self, v):
        calls["label_sets"] += 1
        self._text = v
adt.label = types.ModuleType("adafruit_display_text.label")
adt.label.Label = Label
sys.modules["adafruit_display_text"] = adt
sys.modules["adafruit_display_text.label"] = adt.label

mq = types.ModuleType("adafruit_miniqr")
mq.L = 1
class _Matrix:
    def __init__(self, w=29): self.width = w
    def __getitem__(self, k): return (k[0] + k[1]) % 3 == 0
class QRCode:
    def __init__(self, error_correct=None): self._m = None
    def add_data(self, payload): self._payload = payload
    def make(self):
        calls["qr_makes"] += 1
        self._m = _Matrix()
    @property
    def matrix(self): return self._m
mq.QRCode = QRCode
sys.modules["adafruit_miniqr"] = mq

for name in ("wifi","socketpool","ssl","adafruit_requests","adafruit_connection_manager",
             "espidf","microcontroller","rtc","storage","mdns"):
    sys.modules.setdefault(name, types.ModuleType(name))

board = types.ModuleType("board")
class _Display:
    width = 240; height = 135; rotation = 180; brightness = 1.0
    root_group = None
board.DISPLAY = _Display()
sys.modules["board"] = board


# ---- import firmware ----------------------------------------------------
from knowco2 import state, config, runtime
runtime.poll_buttons = lambda: calls.__setitem__("polls", calls["polls"] + 1)

import knowco2.ui.screens as screens

# minimal widget shim
class _W:
    class display: width = 240; height = 135
    main_group = Group()
    GRAPH_WIDTH = 240; GRAPH_HEIGHT = 100
for name in ("ap_ssid_label","ap_pass_label","ap_ip_label","ap_batt_label",
             "ap_hw_label","ap_scd_label","ap_fw_label"):
    setattr(_W, name, Label(None))
screens.W = _W

state.settings = {"ap_ssid": "knowco2-5c04", "ap_password": "pass12345"}
state.wifi_mode = config.WIFI_MODE_STA
state.mdns_hostname = "knowco2-5c04"
state.ip_str_cached = "192.168.4.47"
state.screen = config.SCREEN_APINFO
state._qr_page = 0
state.hwid_hex = "8B8F265D5C04"; state.scd_serial_str = "F0228B073B58"
state.sensor_model_str = "SCD41"
state.cached_vbat = 4.07; state.cached_pct = 93

fails = []
def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond: fails.append(name)

# T1: STA entry generates exactly ONE QR code (was three)
calls["qr_makes"] = 0
screens.make_or_update_qrs("knowco2-5c04", "pass12345", "192.168.4.47")
check("T1 STA build = exactly 1 generation", calls["qr_makes"] == 1,
      "| makes=%d" % calls["qr_makes"])

# T2: repeat call is a pure cache hit — zero generations, zero group churn
calls["qr_makes"] = 0
n_group = len(_W.main_group)
screens.make_or_update_qrs("knowco2-5c04", "pass12345", "192.168.4.47")
check("T2 repeat entry = 0 generations", calls["qr_makes"] == 0)
check("T2 no display-group churn on cache hit", len(_W.main_group) == n_group)

# T3: AP page toggle uses matrix cache after first build of each page
state.wifi_mode = config.WIFI_MODE_AP
state._qr_page = 0
calls["qr_makes"] = 0
screens.make_or_update_qrs("knowco2-5c04", "pass12345", "192.168.4.1")
check("T3 AP page0 first build = 1 generation", calls["qr_makes"] == 1)
state._qr_page = 1
screens.make_or_update_qrs("knowco2-5c04", "pass12345", "192.168.4.1")
check("T3 AP page1 first build = 1 more", calls["qr_makes"] == 2)
state._qr_page = 0
screens.make_or_update_qrs("knowco2-5c04", "pass12345", "192.168.4.1")
check("T3 back to page0 = cache hit (still 2)", calls["qr_makes"] == 2)

# T4: buttons polled during build (no dropped presses)
check("T4 poll_buttons called during builds", calls["polls"] >= 4,
      "| polls=%d" % calls["polls"])

# T5: refresh_apinfo_screen defers QR (flag) and guards label writes
state.wifi_mode = config.WIFI_MODE_STA
state.qr_refresh_needed = False
screens.refresh_apinfo_screen()
check("T5 refresh sets deferred flag", state.qr_refresh_needed is True)
first_sets = calls["label_sets"]
screens.refresh_apinfo_screen()   # nothing changed -> no label writes
check("T5 unchanged refresh writes 0 labels", calls["label_sets"] == first_sets,
      "| delta=%d" % (calls["label_sets"] - first_sets))
state.cached_pct = 92             # one value changes -> exactly one write
screens.refresh_apinfo_screen()
check("T5 one change = one label write", calls["label_sets"] == first_sets + 1)

# T6: pending-press contract -- the C handler consumes _btn_c_pending
# (code.py needs real hardware to import; verify the source contract)
code_src = open("../code.py").read()
check("T6 poll captures C edges", "state._btn_c_pending = True" in code_src)
check("T6 C handler consumes pending",
      "(not c_now) and (state.prev_c or state._btn_c_pending)" in code_src)
check("T6 deferred QR handler present in main loop",
      "state.qr_refresh_needed and state.screen == config.SCREEN_APINFO" in code_src)
check("T6 no synchronous QR calls left on button path",
      "ui.make_or_update_qrs" not in code_src.split("def _poll_buttons")[1].split("# Deferred QR rebuild")[0])

print()
if fails:
    print("FAILURES:", fails); sys.exit(1)
print("ALL UI TESTS PASSED")
