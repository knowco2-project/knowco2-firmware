#!/usr/bin/env python3
"""OTA transactional-install test: runs the REAL _process_zip_update from
RC-48v2 routes.py against the real OTA zip inside this sandbox's root fs.
Scenarios: happy path, corrupted entry, truncated zip, staging cleanup."""
import sys, types, os, shutil, zlib, time as _t

sys.path.insert(0, "..")

# ---- mocks (CP-only modules) -----------------------------------------
for name in ("wifi", "socketpool", "ssl", "adafruit_requests",
             "adafruit_connection_manager", "espidf", "rtc", "storage",
             "mdns", "board", "displayio", "digitalio", "supervisor_x"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["ssl"].create_default_context = lambda: object()

mc = types.ModuleType("microcontroller")
mc.nvm = None
resets = {"n": 0}
def _rst(): resets["n"] += 1
mc.reset = _rst
sys.modules["microcontroller"] = mc

from knowco2 import state
state._wd = None
state.settings = {}

import knowco2.runtime as runtime
runtime.show_status = lambda *a, **k: None

import knowco2.web.routes as routes
import time
_orig_sleep = time.sleep
time.sleep = lambda s: None  # skip the 3s pre-reboot wait

# capture OTA results instead of a socket
results = []
def fake_send_ota_result(conn, success, message):
    results.append((success, message))
routes._send_ota_result = fake_send_ota_result

ZIP = "BUILD_ZIP_PATH"
fails = []
def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond: fails.append(name)

def clean_root():
    for p in ("/code.py", "/code.py.bak", "/boot.py", "/knowco2", "/ota_staged", "/tmp_ota.zip"):
        if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p): os.remove(p)

def snapshot(root="/knowco2"):
    out = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[p] = zlib.crc32(fh.read())
    return out

# ---- T1: happy path ---------------------------------------------------
clean_root()
shutil.copy(ZIP, "/tmp_ota.zip")
results.clear()
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
ok, msg = results[-1]
check("T1 install succeeded", ok, "| " + msg[:90])
check("T1 55 files CRC-verified", "55 files" in msg)
check("T1 helpers.py intact with log()", os.path.exists("/knowco2/helpers.py")
      and b"def log(" in open("/knowco2/helpers.py","rb").read())
check("T1 code.py installed + version v4",
      b"RC-51-Perf-v1" in open("/knowco2/version.py","rb").read())
check("T1 staging removed", not os.path.exists("/ota_staged"))
check("T1 zip removed", not os.path.exists("/tmp_ota.zip"))
check("T1 reboot attempted", resets["n"] == 1)

good_tree = snapshot()

# ---- T2: corrupted entry → abort, tree untouched ----------------------
data = open(ZIP, "rb").read()
# flip bytes ~40% into the archive (inside some entry's compressed data)
pos = int(len(data) * 0.4)
bad = data[:pos] + bytes(b ^ 0xFF for b in data[pos:pos+8]) + data[pos+8:]
open("/tmp_ota.zip","wb").write(bad)
results.clear(); resets["n"] = 0
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
ok, msg = results[-1]
check("T2 corrupt zip rejected", not ok, "| " + msg[:110])
check("T2 message says nothing changed", "Nothing was changed" in msg)
check("T2 live tree byte-identical", snapshot() == good_tree)
check("T2 no staging leftovers", not os.path.exists("/ota_staged"))
check("T2 no reboot", resets["n"] == 0)

# ---- T3: truncated zip (simulates dropped upload) ---------------------
open("/tmp_ota.zip","wb").write(data[:len(data)//2])
results.clear()
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
ok, msg = results[-1]
check("T3 truncated zip rejected", not ok, "| " + msg[:110])
check("T3 live tree byte-identical", snapshot() == good_tree)
check("T3 no staging leftovers", not os.path.exists("/ota_staged"))

# ---- T4: stale staging dir from an interrupted OTA is cleared ---------
os.makedirs("/ota_staged/knowco2", exist_ok=True)
open("/ota_staged/knowco2/junk.py","w").write("truncated garba")
shutil.copy(ZIP, "/tmp_ota.zip")
results.clear(); resets["n"] = 0
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
ok, msg = results[-1]
check("T4 succeeds despite stale staging", ok)
check("T4 stale staging cleared", not os.path.exists("/ota_staged"))

# ---- T5: single-byte flip inside ONE file is caught by CRC ------------
# Rebuild a zip where knowco2/helpers.py content is corrupted but sizes kept
import zipfile
src = zipfile.ZipFile(ZIP)
with zipfile.ZipFile("/tmp_ota.zip", "w") as dst:
    for info in src.infolist():
        raw = src.read(info.filename)
        if info.filename == "knowco2/helpers.py":
            raw = raw.replace(b"def log(", b"dEf log(", 1)
            # keep declared CRC = original (mismatch on purpose)
            zi = zipfile.ZipInfo(info.filename)
            dst.writestr(zi, raw)
            # overwrite central-dir CRC with the ORIGINAL to force mismatch
            dst.infolist()[-1].CRC = info.CRC
        else:
            dst.writestr(info, raw)
results.clear()
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
ok, msg = results[-1]
check("T5 CRC mismatch rejected", (not ok) and ("CRC" in msg), "| " + msg[:110])
check("T5 live tree byte-identical", snapshot() == good_tree)


# ---- T6: LOW-SPACE PER-FILE MODE --------------------------------------
clean_root()
shutil.copy(ZIP, "/tmp_ota.zip")
results.clear(); resets["n"] = 0
orig_free = routes._fs_free_bytes
routes._fs_free_bytes = lambda: 120_000   # < 423KB tree, > largest file
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
routes._fs_free_bytes = orig_free
ok, msg = results[-1]
check("T6 per-file mode succeeds", ok, "| " + msg[:90])
check("T6 summary says per-file", "per-file mode" in msg)
check("T6 tree matches good install", snapshot() == good_tree)
check("T6 staging removed", not os.path.exists("/ota_staged"))
check("T6 reboot attempted", resets["n"] == 1)

# ---- T7: per-file mode aborts when even one file can't fit ------------
shutil.copy(ZIP, "/tmp_ota.zip")
results.clear(); resets["n"] = 0
routes._fs_free_bytes = lambda: 10_000    # < largest file
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
routes._fs_free_bytes = orig_free
ok, msg = results[-1]
check("T7 rejects impossible install", not ok, "| " + msg[:90])
check("T7 live tree untouched", snapshot() == good_tree)
check("T7 no reboot", resets["n"] == 0)

# ---- T8: per-file mode corrupt-mid-stream stops with complete files ---
data2 = open(ZIP, "rb").read()
pos = int(len(data2) * 0.5)
bad2 = data2[:pos] + bytes(b ^ 0xFF for b in data2[pos:pos+8]) + data2[pos+8:]
open("/tmp_ota.zip","wb").write(bad2)
results.clear(); resets["n"] = 0
routes._fs_free_bytes = lambda: 120_000
routes._process_zip_update(conn=types.SimpleNamespace(send=lambda *a: 0), zip_path="/tmp_ota.zip")
routes._fs_free_bytes = orig_free
ok, msg = results[-1]
check("T8 per-file corrupt stops", not ok, "| " + msg[:110])
check("T8 message says re-run", "re-run" in msg.lower())
check("T8 all files on disk complete (parse+compile ok)",
      all(compile(open(p).read(), p, "exec") or True
          for p in [os.path.join(dp, f) for dp, _, fs in os.walk("/knowco2")
                    for f in fs if f.endswith(".py")]))
check("T8 no reboot on partial", resets["n"] == 0)

# ---- T9: junk cleanup reclaims macOS metadata + stale artifacts --------
os.makedirs("/knowco2/.fseventsd", exist_ok=True)
open("/knowco2/.fseventsd/log1","w").write("x" * 5000)
open("/knowco2/.DS_Store","w").write("x" * 6148)
open("/knowco2/._helpers.py","w").write("x" * 4096)
open("/code.py.ota","w").write("x" * 2000)
freed = routes._fs_junk_cleanup("/knowco2")
check("T9 junk cleanup freed bytes", freed >= 5000 + 6148 + 4096 + 2000, "| freed=%d" % freed)
check("T9 metadata gone", not os.path.exists("/knowco2/.DS_Store")
      and not os.path.exists("/knowco2/._helpers.py")
      and not os.path.exists("/knowco2/.fseventsd")
      and not os.path.exists("/code.py.ota"))
check("T9 real files untouched", snapshot() == good_tree)

clean_root()
time.sleep = _orig_sleep
print()
if fails:
    print("FAILURES:", fails); sys.exit(1)
print("ALL OTA TESTS PASSED")
