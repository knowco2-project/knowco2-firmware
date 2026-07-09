#!/usr/bin/env python3
"""Smoke test for RC-48 cloud self-heal: mocks the CircuitPython hardware
stack and exercises cloud_send through pre-flight skip, MemoryError
teardown, consecutive-failure reset, and success-resets-counter paths."""
import sys, types, json, time as _time

sys.path.insert(0, "..")

# ---- mock CircuitPython-only modules ---------------------------------
calls = {"cm_close_all": 0, "mcu_reset": 0, "posts": 0}

wifi_m = types.ModuleType("wifi")
class _Radio:
    connected = True
    ipv4_address = "192.168.4.47"
wifi_m.radio = _Radio()
sys.modules["wifi"] = wifi_m

sp_m = types.ModuleType("socketpool")
class SocketPool:
    AF_INET = 2; SOCK_STREAM = 1
    def __init__(self, radio): pass
sp_m.SocketPool = SocketPool
sys.modules["socketpool"] = sp_m

ssl_m = types.ModuleType("ssl")
ssl_m.create_default_context = lambda: object()
sys.modules["ssl"] = ssl_m

# adafruit_requests mock — behavior switched per-test via POST_BEHAVIOR
POST_BEHAVIOR = {"mode": "ok"}
req_m = types.ModuleType("adafruit_requests")
class _Resp:
    status_code = 200
    text = "ok"
    def close(self): pass
class Session:
    def __init__(self, pool, ctx): pass
    def post(self, url, data=None, headers=None, timeout=None):
        calls["posts"] += 1
        if POST_BEHAVIOR["mode"] == "memerr":
            raise MemoryError()
        if POST_BEHAVIOR["mode"] == "oos":
            raise RuntimeError("Out of sockets")
        if POST_BEHAVIOR["mode"] == "oserr":
            raise OSError(113, "EHOSTUNREACH")
        return _Resp()
req_m.Session = Session
sys.modules["adafruit_requests"] = req_m

acm_m = types.ModuleType("adafruit_connection_manager")
def connection_manager_close_all(pool=None, release_references=False):
    calls["cm_close_all"] += 1
acm_m.connection_manager_close_all = connection_manager_close_all
sys.modules["adafruit_connection_manager"] = acm_m

espidf_m = types.ModuleType("espidf")
IDF = {"free": 120000, "big": 80000}
espidf_m.heap_caps_get_free_size = lambda: IDF["free"]
espidf_m.heap_caps_get_largest_free_block = lambda: IDF["big"]
sys.modules["espidf"] = espidf_m

mc_m = types.ModuleType("microcontroller")
def _reset():
    calls["mcu_reset"] += 1
    raise SystemExit("MCU RESET")  # simulate reboot
mc_m.reset = _reset
mc_m.nvm = None
sys.modules["microcontroller"] = mc_m

for name in ("rtc", "storage", "mdns"):
    sys.modules[name] = types.ModuleType(name)

# ---- import firmware modules ----------------------------------------
from knowco2 import state, config
config_sleep_patch = None
from knowco2.telemetry import cloud
cloud.time.sleep = lambda s: None  # skip retry pauses in tests

# minimal runtime wiring
state.settings = {"device_id": "KC2-TEST"}
state.cloud_enabled = True
state.cloud_api_url = "https://api.knowco2.com"
state.cloud_device_token = "AAAA"
state.wifi_mode = config.WIFI_MODE_STA
state.socket_pool = None
state._wd = None
state.boot_time_mono = _time.monotonic() - 100000  # long uptime

# stub crypto to avoid hashlib specifics
from knowco2 import crypto as crypto_mod
crypto_mod.decode_token_to_bytes = lambda t: b"\x00" * 32
crypto_mod.hmac_sha256_digest = lambda k, m: b"\x11" * 32
crypto_mod.b64encode_bytes = lambda b: "sig"

# stub wifi helper + runtime UI
import knowco2.net.wifi as wmod
wmod.ensure_sta_connected = lambda: True
import knowco2.runtime as runtime
runtime.show_status = lambda *a, **k: None

payload = {"co2": 622}
fails = []

def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond:
        fails.append(name)

# T1: healthy path — success, counters clear
POST_BEHAVIOR["mode"] = "ok"
ok = cloud.cloud_send(payload)
check("T1 success returns True", ok is True)
check("T1 memerr counter is 0", state.cloud_consec_memerr == 0)
check("T1 idf stats exported", state.idf_free == 120000 and state.idf_largest_block == 80000)

# T2: pre-flight skip when largest block below threshold; no POST fired
IDF["big"] = 8000
posts_before = calls["posts"]
ok = cloud.cloud_send(payload)
check("T2 low-heap pre-flight returns False", ok is False)
check("T2 no POST attempted", calls["posts"] == posts_before)
check("T2 counter incremented", state.cloud_consec_memerr == 1)
check("T2 teardown ran", calls["cm_close_all"] >= 1)
check("T2 error message set", "IDF heap low" in state.cloud_last_error)
IDF["big"] = 80000

# T3: MemoryError during POST — teardown + counter, session rebuilt next time
POST_BEHAVIOR["mode"] = "memerr"
before_cm = calls["cm_close_all"]
before_posts = calls["posts"]
ok = cloud.cloud_send(payload)
check("T3 MemoryError returns False", ok is False)
check("T3 counter now 2", state.cloud_consec_memerr == 2)
check("T3 retried once before failing", calls["posts"] == before_posts + 2)
check("T3 teardowns ran (retry + final)", calls["cm_close_all"] == before_cm + 2)
check("T3 session dropped", state.cloud_session is None and state.cloud_ctx is None)
check("T3 error recorded", state.cloud_last_error == "MemoryError()")

# T4: "Out of sockets" RuntimeError also counts as mem-class
POST_BEHAVIOR["mode"] = "oos"
ok = cloud.cloud_send(payload)
check("T4 counter now 3", state.cloud_consec_memerr == 3)

# T5: ordinary OSError does NOT count toward memerr and does NOT teardown
POST_BEHAVIOR["mode"] = "oserr"
before_cm = calls["cm_close_all"]
ok = cloud.cloud_send(payload)
check("T5 OSError returns False", ok is False)
check("T5 counter unchanged", state.cloud_consec_memerr == 3)
check("T5 no teardown for plain OSError", calls["cm_close_all"] == before_cm)

# T6: success resets the counter
POST_BEHAVIOR["mode"] = "ok"
ok = cloud.cloud_send(payload)
check("T6 success resets counter", ok is True and state.cloud_consec_memerr == 0)

# T7: reaching CLOUD_MEMERR_RESET_AFTER triggers microcontroller.reset()
POST_BEHAVIOR["mode"] = "memerr"
reset_hit = False
try:
    for _ in range(config.CLOUD_MEMERR_RESET_AFTER + 1):
        cloud.cloud_send(payload)
except SystemExit:
    reset_hit = True
check("T7 hard reset after %d consecutive" % config.CLOUD_MEMERR_RESET_AFTER,
      reset_hit and calls["mcu_reset"] == 1
      and state.cloud_consec_memerr == config.CLOUD_MEMERR_RESET_AFTER)

# T8: reset suppressed during early uptime (reboot-loop guard)
calls["mcu_reset"] = 0
state.cloud_consec_memerr = 0
state.boot_time_mono = _time.monotonic()  # just booted
for _ in range(config.CLOUD_MEMERR_RESET_AFTER + 3):
    cloud.cloud_send(payload)
check("T8 no reset within min-uptime window", calls["mcu_reset"] == 0)

# T9: reset deferred while the web UI is in active use
calls["mcu_reset"] = 0
state.cloud_consec_memerr = 0
state.boot_time_mono = _time.monotonic() - 100000  # long uptime again
state.last_http_ts = _time.monotonic()             # someone is on the web UI
POST_BEHAVIOR["mode"] = "memerr"
for _ in range(config.CLOUD_MEMERR_RESET_AFTER + 3):
    cloud.cloud_send(payload)
check("T9 reset deferred during web activity", calls["mcu_reset"] == 0)
state.last_http_ts = _time.monotonic() - config.CLOUD_MEMERR_RESET_WEB_GRACE_S - 10
reset_hit2 = False
try:
    cloud.cloud_send(payload)
except SystemExit:
    reset_hit2 = True
check("T9 reset resumes after grace expires", reset_hit2 and calls["mcu_reset"] == 1)

# T10: transient failure -- first post raises, retry succeeds
state.cloud_consec_memerr = 0
transient = {"left": 1}
class _TransientSession:
    def __init__(self, *a): pass
    def post(self, *a, **k):
        calls["posts"] += 1
        if transient["left"] > 0:
            transient["left"] -= 1
            raise MemoryError()
        return _Resp()
req_m.Session = _TransientSession
state.cloud_session = None; state.cloud_ctx = None
ok = cloud.cloud_send(payload)
check("T10 transient failure recovered by in-send retry", ok is True)
check("T10 counter stays 0 after recovery", state.cloud_consec_memerr == 0)
req_m.Session = Session

print()
if fails:
    print("FAILURES:", fails); sys.exit(1)
print("ALL TESTS PASSED")
