# KnowCO2 Firmware — CircuitPython

Firmware license: [MIT](LICENSE)

## Overview

The KnowCO2 firmware runs on [CircuitPython](https://circuitpython.org) on the **Adafruit Feather ESP32-S3 Reverse TFT**. It reads CO₂, temperature, and humidity from an SCD40/SCD41 or SCD30 sensor and displays them locally while optionally publishing to MQTT and cloud services.

---

## Installing on Your Device

Choose one of the options below depending on your situation.

### Option 1 — Full ZIP (fresh install or upgrade)

Download `knowco2-full-vX.Y.Z.zip` from the [latest release](../../releases/latest).

1. Connect the Feather via USB — the `CIRCUITPY` drive appears.
   *(Hold button B at power-up to force the drive visible if it is hidden.)*
2. Unzip and copy everything inside onto the root of `CIRCUITPY`, replacing existing files.
3. Eject safely and reboot — the device boots into KnowCO2.

### Option 2 — OTA ZIP (upgrade via the web portal)

Download `knowco2-ota-vX.Y.Z.zip` from the [latest release](../../releases/latest).

1. Open `http://knowco2-XXXX.local/update` in a browser on the same network.
2. Upload the OTA ZIP and wait for the device to apply and reboot.

### Option 3 — Source code

The full source is available as `Source code (zip)` on every release, or by cloning this repo. It is plain CircuitPython — no build step required.

### Installing CircuitPython (first time only)

The device needs CircuitPython **10.x** installed before the firmware can run:

1. Download the latest **10.x** `.uf2` for the [Adafruit Feather ESP32-S3 Reverse TFT](https://circuitpython.org/board/adafruit_feather_esp32s3_reverse_tft/).
2. Double-tap the reset button — the `FTHRS3BOOT` drive appears.
3. Drag the `.uf2` onto the drive — the board reboots and `CIRCUITPY` appears.
4. Then follow **Option 1** above to install the firmware.

## Fast onboarding

Connect to the WPA-protected `knowco2-…` setup network and open
`http://192.168.4.1`. The Fast device setup card saves Wi-Fi and can also
accept the 8-character, one-time code created in the signed-in cloud portal.
The app path sends a high-entropy activation token to the same local API.

The browser/app never receives the permanent device secret. The device leaves
AP mode only after returning a complete response, redeems the temporary claim
at the fixed `https://api.knowco2.com/v1/devices/activate` endpoint, stores the
returned `cloud_device_id` and secret, then continues to use the existing
HMAC-authenticated `/v1/ingest` protocol. A failed Wi-Fi connection restores
the setup AP. A power loss before activation clears the RAM-only claim, so the
user must create a new one-time code.

Local API v1 routes are JSON and intentionally do not enable wildcard CORS on
writes:

| Route | Purpose |
|---|---|
| `GET /api/v1/info` | Secret-free identity, capability, Wi-Fi, and cloud state |
| `GET/PATCH /api/v1/settings` | Public settings only; credentials are excluded |
| `POST /api/v1/wifi` | Write-only WPA/WPA2 credentials |
| `POST /api/v1/cloud/claim` | Write-only one-time cloud credential |
| `POST /api/v1/onboarding` | Atomic browser/app Wi-Fi plus optional cloud setup |
| `POST /api/v1/cloud/enabled` | Explicitly enable or disable uploads |
| `POST /api/v1/cloud/credentials/forget` | Forget local credentials only; does not release cloud ownership |

Provisioning writes are allowed on the physical setup AP. On a shared STA
network they require both the configured admin password and the temporary
physical-unlock window.

---

## Repository Layout

```
knowco2-firmware/
├── code.py              # Entry point — boot sequence and main loop
├── boot.py              # Runs at power-up (USB drive mode, display init)
├── settings.json        # User settings (created automatically on first boot)
├── assets/
│   └── splash.bmp       # Boot logo
├── knowco2/             # Firmware package
│   ├── version.py       # Firmware version string
│   ├── config.py        # Constants and thresholds
│   ├── state.py         # Shared runtime state
│   ├── runtime.py       # Module registry and main-loop orchestration
│   ├── helpers.py       # Utilities
│   ├── battery.py       # Battery monitor (MAX17048)
│   ├── crypto.py        # Device ID / token helpers
│   ├── ids.py           # Hardware identity
│   ├── settings.py      # Persistent settings read/write
│   ├── sensors/         # Sensor drivers (SCD4x, SCD30, base class)
│   ├── net/             # WiFi, NTP
│   ├── telemetry/       # Cloud publish, MQTT
│   ├── ui/              # Display widgets and screen logic
│   └── web/             # HTTP portal, OTA update handler
└── lib/                 # Third-party CircuitPython libraries (see below)
```

---

## Dependencies

All libraries in `lib/` are from the official [Adafruit CircuitPython Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle) (MIT licensed). The pre-compiled `.mpy` snapshot shipped in this repo targets **CircuitPython 10.x**. You can also install them yourself with [`circup`](https://github.com/adafruit/circup):

```
circup install adafruit_scd4x adafruit_scd30 adafruit_max1704x \
               adafruit_minimqtt adafruit_requests adafruit_ntp \
               adafruit_httpserver adafruit_display_text adafruit_miniqr \
               adafruit_connection_manager adafruit_ticks adafruit_hashlib \
               adafruit_register adafruit_stcc4
```

| Library                       | Purpose                         |
|-------------------------------|---------------------------------|
| `adafruit_scd4x`              | SCD40 / SCD41 CO₂ sensor driver |
| `adafruit_scd30`              | SCD30 CO₂ sensor driver         |
| `adafruit_stcc4`              | stcc4 CO₂ sensor driver         |
| `adafruit_max1704x`           | Battery fuel-gauge (MAX17048)   |
| `adafruit_minimqtt`           | MQTT client                     |
| `adafruit_requests`           | HTTP client                     |
| `adafruit_ntp`                | NTP time sync                   |
| `adafruit_httpserver`         | Local web portal                |
| `adafruit_display_text`       | TFT text rendering              |
| `adafruit_miniqr`             | QR code generation              |
| `adafruit_connection_manager` | Socket pool helper              |
| `adafruit_ticks`              | Tick/timeout utilities          |
| `adafruit_hashlib`            | SHA / HMAC for cloud auth       |
| `adafruit_register`           | I²C register helpers            |

---

## Buttons

| Button | Pin | Action |
|---|---|---|
| A | D0 | Toggle °C / °F |
| B | D1 | Cycle display mode (Text → Big CO₂ → Graph) |
| C | D2 | Info screen |
| Hold B at power-up | D1 | Mount `CIRCUITPY` USB drive for file access |

---

## OTA Updates

Navigate to `http://knowco2-XXXX.local/update` (replace `XXXX` with your device's last four ID characters). You can upload:

- A single `code.py` — replaces the main script only.
- A full OTA ZIP — installs `code.py`, `boot.py`, the `knowco2/` package, `lib/`, and `assets/` in one shot.

The previous `code.py` is kept as `/code.py.bak` automatically.

---

## License

This firmware is MIT licensed — see [LICENSE](LICENSE).

The `lib/` directory contains pre-compiled Adafruit CircuitPython libraries distributed under their own MIT licenses. See the [Adafruit CircuitPython Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle) for individual library sources and license texts.
