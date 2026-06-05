# KnowCO2 firmware — modular architecture

This is a **test/reference decomposition** of the single-file firmware
(`model-a-version-RC-43-Energy-5-7.py`, ~6,500 lines). It splits the code into
a package with a clear sensor-driver abstraction, and lays out a safe path to
finish the migration without ever breaking the working firmware.

## What's in this build

```
knowco2_modular/
├── code.py                     # full firmware entry point (boot + loop)  [final step]
├── boot.py                     # ✅ runs at power-up (USB drive + display)
├── sensor_smoketest.py         # ✅ runnable on-hardware test of the sensor layer
├── ARCHITECTURE.md             # this file
├── DEPLOY.md                   # ✅ on-device CIRCUITPY layout + libraries + OTA
├── ADDING_A_SENSOR.md          # contributor guide
├── test_sensors.py             # ✅ desktop tests for the sensor layer (mock sensors)
└── knowco2/
    ├── __init__.py
    ├── version.py  config.py  helpers.py  state.py  runtime.py   # ✅ core
    ├── crypto.py  settings.py  battery.py  ids.py                # ✅ leaf/IO
    ├── sensors/   (base, scd4x, scd30, __init__)                 # ✅ sensor abstraction
    ├── net/       (wifi, ntp, __init__)                          # ✅ connectivity
    ├── telemetry/ (cloud, mqtt, __init__)                        # ✅ outbound data
    └── web/
        ├── http_util.py        # ✅ raw-socket HTTP helpers + OTA streaming
        ├── portal_page.py      # ✅ config portal HTML (verbatim, ~1,300 lines)
        └── routes.py           # ✅ route handlers + server loop + OTA-ZIP
```

✅ = implemented and verified (compiles, imports with no circular dependencies,
passes functional smoke tests with the hardware libraries absent). The sensor
layer is additionally verified with mock sensors, and the route handlers with a
fake socket (/status, /data, /calibration, settings POST, OTA form).

**Still in the main file** (the final step): the display widgets + UI logic and
the boot sequence + main loop. Everything they depend on is now modular, and the
UI side-effects routes/net/telemetry need are already exposed as `runtime`
hooks, so the final `ui/` + `code.py` port is mechanical wiring.

---

## The one hard problem: shared global state

The original file works because all ~80 mutable variables live in a single
module namespace, and functions mutate them with `global`. The instant code is
split across files this breaks — **Python's `global` only reaches the current
module's namespace.** A `global last_co2` in `sensor.py` and the same line in
`ui.py` refer to two unrelated variables, so writes silently fail to propagate.

The fix is `knowco2/state.py`: one module that holds all cross-module mutable
state as attributes. Everyone imports it and reads/writes through it:

```python
from knowco2 import state
state.last_co2 = co2              # write — visible everywhere
if state.last_co2 is not None:    # read
    ...
```

Constants that never change at runtime stay in `config.py`. The split between
the two is intentional and load-bearing.

**Migrating a function is mechanical:**
1. delete its `global X, Y` line,
2. prefix each shared name with `state.`,
3. leave genuinely-local variables alone.

That's it. No logic changes.

---

## Breaking the dependency cycle: `runtime.py`

There's one structural problem beyond shared state. The networking code reports
progress to the UI (`show_status`), asks the web layer to start the HTTP
server, and asks the UI to redraw QR codes — while the UI and web layers in turn
drive networking. If every module imported the others directly, Python would hit
an **import-time cycle**.

`knowco2/runtime.py` is a tiny hook registry that breaks this. The lower layers
(`net`, `telemetry`, `settings`) call hooks like `runtime.show_status(...)`
instead of importing the UI. At boot, the UI/web/boot code installs the real
implementations once:

```python
from knowco2 import runtime, ui, web
runtime.register(
    show_status=ui.show_status,
    update_wifi_indicator=ui.update_wifi_indicator,
    make_or_update_qrs=ui.make_or_update_qrs,
    refresh_apinfo_screen=ui.refresh_apinfo_screen,
    apply_color_scheme=ui.apply_color_scheme,
    start_http_server=web.start_http_server,
)
```

Until then the hooks are safe no-ops, so any module can be imported and tested
on its own. The result is an **acyclic** graph: `net`/`telemetry` depend only on
`state`, `config`, `crypto`, and `runtime` — never on `ui` or `web`.

One related move: the reusable cloud TLS session/context now live in
`state.cloud_session` / `state.cloud_ctx`. That lets `net/wifi.py` invalidate
them on a Wi-Fi mode change (by nulling those fields) without importing the
`telemetry` package — which would otherwise create a cycle.

---

## How the firmware uses the sensor package

This is the part that proves "functionality intact": the proven application
logic is unchanged — only the sensor-specific branching is replaced by calls
into the package. Below is the before → after for each of the five places the
old code touched the sensor directly.

### 1. Init + calibration (old lines ~1486–1565)

```python
from knowco2 import sensors, state

i2c = board.I2C()
state.sensor = sensors.detect_sensor(i2c)      # tries SCD4x then SCD30
if state.sensor is None:
    state.scd_init_failed = True
    state.last_scd_sample_ts = time.monotonic()
    show_status("Sensor init failed")
else:
    state.sensor_model_str = state.sensor.model
    state.scd_serial_str   = state.sensor.read_serial()
    status_label.text = "Warming up..."
    time.sleep(5)
    status_label.text = ""
    state.last_scd_sample_ts = time.monotonic()
    # stored calibration/compensation — no hasattr() branching needed
    state.sensor.set_asc(state.settings.get("asc_enabled", True))
    state.sensor.set_altitude(state.settings.get("altitude", 0))
    state.sensor.set_ambient_pressure(state.settings.get("ambient_pressure", 0))
```

### 2. Read loop (old lines ~6300–6307)

```python
s = state.sensor
if s is not None:
    try:
        if s.data_ready:                       # was: data_available vs data_ready
            co2, temp_c, rh = s.read()          # was: scd.CO2 / .temperature / .relative_humidity
            state.scd_crc_failures = 0
            state.scd_recoveries = 0
            # ... unchanged: rate-of-change, history, alerts, redraw ...
    except RuntimeError as err:
        state.scd_crc_failures += 1
        if state.scd_crc_failures >= config.SCD_MAX_FAILS_BEFORE_RESET:
            scd_recover()
```

### 3. Recovery (old lines ~1602–1671)

```python
def scd_recover():
    s = state.sensor
    if s is None:
        return
    now = time.monotonic()
    if state._wd is not None:
        try: state._wd.feed()
        except Exception: pass
    if (now - state.last_scd_reset) < config.SCD_RESET_COOLDOWN_SEC:
        return
    state.last_scd_reset = now
    state.scd_crc_failures = 0
    state.scd_recoveries += 1
    s.recover(low_power=state.energy_mode)     # all stop/reset/restart branching is in the driver
    show_status("SCD: recovered")
    if state.scd_recoveries >= config.SCD_MAX_RECOVERIES_BEFORE_RESET:
        show_status("SCD: restarting")
        time.sleep(0.5)
        if microcontroller is not None:
            microcontroller.reset()
        state.scd_recoveries = 0
```

### 4. Low-power switch (old lines ~5949–5973)

```python
# inside apply_energy_mode(active):
if state.sensor is not None:
    state._scd_period_effective = state.sensor.set_low_power(active)
    # returns 30.0 for SCD41 LP, else 5.0 — staleness watchdog scales correctly
```

### 5. Forced calibration (old body of perform_force_calibration)

```python
if state.sensor is None or not state.sensor.force_calibration(target):
    show_status("Calibration failed")
    return False
# ... unchanged: record last_calibration_ts/ref, save_settings(), status ...
return True
```

Every `hasattr(scd, "...")` in the firmware is now gone. Adding a sensor can
never require editing any of the five blocks above.

---

## Safe incremental migration plan

A big-bang rewrite of all 6,500 lines in one pass is the wrong move for a
known-good firmware: the display/HTTP/loop layer is deeply coupled to shared
state, and there's no way to verify a blind relocation except on hardware. The
**strangler** approach keeps a working device at every step and matches your
RC-iterative workflow (flash, confirm, advance).

Migrate in this order — each step is independently flashable and testable:

| Step | Module(s) | Status | Notes |
|---|---|---|---|
| 0 | `version`, `config`, `helpers` | ✅ done | pure values/functions |
| 1 | `state` | ✅ done | the enabler |
| 2 | `sensors/` | ✅ done | self-contained, mock-tested |
| 3 | `runtime` | ✅ done | hook registry; breaks cycles |
| 4 | `crypto`, `battery`, `ids` | ✅ done | leaf modules |
| 5 | `settings` | ✅ done | load/apply/save + AP creds |
| 6 | `net/` (wifi + mDNS, ntp) | ✅ done | progress via `runtime` hooks |
| 7 | `telemetry/` (cloud, mqtt, aio) | ✅ done | reads `state` readings |
| 8 | `web/http_util` + `web/portal_page` | ✅ done | HTTP plumbing + portal HTML |
| 9 | `web/routes` (handlers + server loop + OTA-ZIP) | ✅ done | dispatch glue; calibration now via the sensor driver |
| 10 | `ui/` (display widgets + redraw/text/graph + QR) | **next** | display objects + most shared state |
| 11 | `code.py` | last | boot sequence + main loop = the wiring |

After each step: flash, confirm identical behaviour, commit. If a step
misbehaves, only that module changed — easy to bisect.

### Wiring the new modules into the main file (for the in-between flash)

Until steps 9–11 are done, the existing single file can already delegate to the
new modules so you can flash and confirm each layer in isolation. The shims are
one-liners, e.g.:

```python
from knowco2 import state, runtime, settings, battery, ids
from knowco2.net import wifi as kc_wifi, ntp as kc_ntp
from knowco2.telemetry import cloud as kc_cloud, mqtt as kc_mqtt

# at boot, after the UI widgets/functions are defined:
runtime.register(show_status=show_status, update_wifi_indicator=update_wifi_indicator,
                 make_or_update_qrs=make_or_update_qrs, refresh_apinfo_screen=refresh_apinfo_screen,
                 apply_color_scheme=apply_color_scheme, start_http_server=start_http_server)

# then the old calls map to:
#   load_settings()        -> settings.load_settings()
#   switch_to_ap()         -> kc_wifi.switch_to_ap()
#   cloud_send(payload)    -> kc_cloud.cloud_send(payload)
#   publish_to_mqtt()      -> kc_mqtt.publish_to_mqtt()
#   ntp_sync()             -> kc_ntp.ntp_sync()
```

Shared values that used to be bare globals are now `state.*` (e.g. `last_co2`
→ `state.last_co2`, `wifi_mode` → `state.wifi_mode`, `settings` →
`state.settings`).

### Verifying off-hardware

Pure modules run under desktop CPython. `test_sensors.py` covers the sensor
layer with mock devices. The connectivity/telemetry/web modules import cleanly
with the hardware libraries absent (the `try/except import` guards exercise the
"library missing" paths), so a CI job can catch import errors, circular
dependencies, and obvious regressions before anything is flashed.

## Why this is the right shape for an open-source project

- **One responsibility per file** — contributors can read `cloud.py` without
  scrolling past the graph renderer.
- **Sensors are pluggable** — the headline win. New hardware = one new file +
  one registry line, with zero risk to unrelated code (`ADDING_A_SENSOR.md`).
- **Stable seams** — the `CO2Sensor` contract and `state` are the interfaces a
  PR is reviewed against, so changes stay local and reviewable.
- **Desktop-testable units** — pure modules (`helpers`, `sensors`, `config`)
  run under CPython with mocks, so CI can catch regressions before flashing.
```
