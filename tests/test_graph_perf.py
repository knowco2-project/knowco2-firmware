#!/usr/bin/env python3
"""RC-51: prove the bitmaptools graph rewrite is pixel-identical to the
pure-Python path, and count the work reduction."""
import sys, types

sys.path.insert(0, "..")

counts = {"setitem": 0, "fills": 0}

# ---- mocks --------------------------------------------------------------
dio = types.ModuleType("displayio")
class Bitmap:
    def __init__(self, w, h, n=8):
        self.width, self.height = w, h
        self.px = {}
    def __setitem__(self, k, v):
        counts["setitem"] += 1
        self.px[k] = v
    def __getitem__(self, k):
        return self.px.get(k, 0)
    def fill(self, v):
        self.px = {}
class Palette:
    def __init__(self, n): pass
    def __setitem__(self, k, v): pass
class TileGrid:
    def __init__(self, *a, **k): pass
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
    def __init__(self, *a, **k):
        self.text = ""; self.anchor_point = (0,0); self.anchored_position = (0,0)
        self.hidden = False; self.scale = 1
adt.label = types.ModuleType("adafruit_display_text.label"); adt.label.Label = Label
sys.modules["adafruit_display_text"] = adt
sys.modules["adafruit_display_text.label"] = adt.label
mq = types.ModuleType("adafruit_miniqr"); mq.L = 1; mq.QRCode = object
sys.modules["adafruit_miniqr"] = mq
board = types.ModuleType("board")
class _D: width=240; height=135; rotation=180; brightness=1.0; root_group=None
board.DISPLAY = _D()
sys.modules["board"] = board
for name in ("wifi","socketpool","ssl","adafruit_requests","adafruit_connection_manager",
             "espidf","microcontroller","rtc","storage","mdns"):
    sys.modules.setdefault(name, types.ModuleType(name))

# native mock: exclusive x2/y2, like real bitmaptools
bt = types.ModuleType("bitmaptools")
def fill_region(bmp, x1, y1, x2, y2, value):
    counts["fills"] += 1
    for y in range(y1, y2):
        for x in range(x1, x2):
            bmp.px[(x, y)] = value   # direct write; not counted as setitem
bt.fill_region = fill_region
sys.modules["bitmaptools"] = bt

from knowco2 import state, config, runtime
runtime.poll_buttons = lambda: None
import knowco2.ui.screens as screens

class _W:
    GRAPH_WIDTH = 240; GRAPH_HEIGHT = 100; GRAPH_Y = 20
    graph_bitmap = Bitmap(240, 100)
    class display: width=240; height=135
    main_group = Group()
for name in ("x_left_label","x_right_label","x_mid_label","y_max_label","y_min_label",
             "low_label","med_label","high_label"):
    setattr(_W, name, Label())
screens.W = _W

# realistic history: ramp + noise, includes clipping at both ends
state.co2_history = [420 + (i * 37) % 1900 for i in range(80)]
state.graph_scale_mode = "auto"
state.LOW_THRESHOLD = 800; state.MED_THRESHOLD = 1200; state.ALERT_THRESHOLD = 1500
state.graph_drawing = False

def render(use_native):
    screens.bitmaptools = bt if use_native else None
    _W.graph_bitmap = Bitmap(240, 100)
    counts["setitem"] = 0; counts["fills"] = 0
    state.graph_drawing = False
    screens.redraw_graph()
    return dict(_W.graph_bitmap.px), counts["setitem"], counts["fills"]

px_python, writes_py, _ = render(False)
px_native, writes_nat, fills = render(True)

fails = []
def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond: fails.append(name)

check("pixel-identical output", px_python == px_native,
      "| python px=%d native px=%d" % (len(px_python), len(px_native)))
if px_python != px_native:
    diff = {k for k in set(px_python) | set(px_native)
            if px_python.get(k, 0) != px_native.get(k, 0)}
    print("   first diffs:", sorted(diff)[:8])
check("python path per-pixel writes are heavy", writes_py > 5000,
      "| %d writes" % writes_py)
check("native path nearly zero Python writes", writes_nat < 50,
      "| %d writes, %d native fills" % (writes_nat, fills))
check("fill count sane (<200)", 0 < fills < 200)

print()
if fails:
    print("FAILURES:", fails); sys.exit(1)
print("GRAPH EQUIVALENCE TEST PASSED — %dx fewer Python pixel ops" %
      (writes_py // max(writes_nat, 1)))
