# Deploying KnowCO2 on the device (CIRCUITPY layout)

This explains exactly what lives where on the board so the modular firmware
runs correctly and stays stable.

## What the board's filesystem should look like

```
CIRCUITPY/                 (the board's flash drive)
├── boot.py                # runs once at power-up (USB-drive + display setup)
├── code.py                # entry point (boot sequence + main loop)   ← final step
├── settings.json          # persisted settings (created automatically)
├── settings.json.bak      # auto-backup (created on save / before OTA)
├── assets/
│   └── splash.bmp         # startup logo
├── knowco2/               # the firmware package (this project)
│   ├── __init__.py
│   ├── version.py  config.py  helpers.py  state.py  runtime.py
│   ├── crypto.py  settings.py  battery.py  ids.py
│   ├── sensors/  (base, scd4x, scd30, __init__)
│   ├── net/      (wifi, ntp, __init__)
│   ├── telemetry/(cloud, mqtt, __init__)
│   └── web/      (http_util, portal_page, routes, __init__)
└── lib/                   # third-party CircuitPython libraries (.mpy)
    ├── adafruit_scd4x.mpy
    ├── adafruit_scd30.mpy            (only if you use an SCD30)
    ├── adafruit_stcc4.mpy             (only if you use an STCC4 — `circup install adafruit_stcc4`)
    ├── adafruit_max1704x.mpy
    ├── adafruit_minimqtt/            (folder)
    ├── adafruit_requests.mpy
    ├── adafruit_miniqr.mpy
    ├── adafruit_display_text/        (folder)
    └── adafruit_connection_manager.mpy   (dependency of requests/minimqtt)
```

The board imports `code.py` at boot; `code.py` imports the `knowco2` package,
which imports the libraries from `lib/`. The package is plain CircuitPython —
it doesn't need to be in `lib/`, and keeping it at the root makes it easy to
read and OTA-update.

## Libraries (`lib/`)

Use the CircuitPython library bundle that matches your CircuitPython version
(10.x). The package imports each optional library defensively, so a missing
one degrades gracefully (e.g. no `adafruit_scd30` just means SCD30 support is
off) rather than crashing — but for normal operation you want the set above.

## OTA updates map straight onto this layout

The `/update` page accepts either a single `code.py` or a full `.zip`. The ZIP
installer now accepts these top-level paths and ignores everything else:

```
code.py   boot.py   knowco2/   lib/   assets/
```

So to ship a full update including the package:

```
zip -r knowco2-update.zip code.py boot.py knowco2/ lib/ assets/
```

`code.py` is validated (must look like Python) and swapped in atomically last,
after the other files are on disk; the previous one is kept as `/code.py.bak`.
Settings are backed up before any write and restored on next boot if needed.

## boot.py

The provided `boot.py` runs before `code.py` and:
- sets the display rotation to 180 and clears the REPL terminal so nothing
  upside-down flashes at boot;
- hides the USB CIRCUITPY drive by default (so end users don't see it) while
  keeping the filesystem writable for settings;
- **hold button D1 at power-up** to keep the USB drive visible (for development
  / editing files from your computer).

This is unchanged from your working build and is compatible with the package.

## Stability notes

- **Compile the package to `.mpy`** for production. Plain `.py` modules are
  compiled in RAM at import; precompiling with `mpy-cross` (matching your
  CircuitPython version) cuts boot-time RAM use and speeds startup. Imports
  happen once at boot, so there's no per-loop cost from the module split.
- **Watchdog**: the hardware watchdog is fed every main-loop iteration and is
  explicitly extended to 90 s and fed per-chunk during OTA writes, so a large
  upload can't trigger a mid-write reset (which would wipe the filesystem).
  This behaviour is preserved in `web/http_util.stream_request_body_to_file`
  and the OTA paths in `web/routes.py`.
- **Sockets**: the firmware reuses one `SocketPool` (`state.socket_pool`) for
  cloud/MQTT/NTP and the HTTP server, rather than creating new pools per call —
  important because the ESP32-S3 has a small fixed socket limit.
- **Memory**: large HTML responses are built and sent, then `gc.collect()` runs
  in the main loop's maintenance block. The `/status` JSON reports `mem_free`,
  `mem_free_min`, and an EMA so you can watch for leaks over time.

## Where this build is

Everything except the **presentation layer and main loop** is now in the
package and verified to import cleanly. The last step is `ui/` (display widgets
+ screen logic) and `code.py` (boot sequence + button handling + main loop),
which wire the package together via `runtime.register(...)`. See ARCHITECTURE.md
for the exact wiring.
