# KnowCO2 Firmware — CircuitPython

Firmware license: [MIT](LICENSE)

## Overview

The KnowCO2 firmware runs on [CircuitPython](https://circuitpython.org) on the **Adafruit Feather ESP32-S3 Reverse TFT**. It reads CO₂, temperature, and humidity from an SCD40/SCD41 or SCD30 sensor and displays them locally while optionally publishing to MQTT and cloud services.

---

## Installing on Your Device

Choose one of the options below depending on your situation.

### Option 1 — Full ZIP (fresh install or upgrade)

Download `knowco2-full-vX.Y.Z.zip` from the [latest release](../../releases/latest).

1. Hold physical button **B** while powering on or resetting the device. The
   `KNOWCO2` service volume appears.
   *(A factory-blank CircuitPython board initially uses `CIRCUITPY`; installing
   KnowCO2 changes the label to `KNOWCO2`.)*
2. Unzip and copy everything inside onto the root of the visible service
   volume, replacing existing files.
3. Eject safely and reboot — the device boots into KnowCO2.

### Option 2 — OTA ZIP (upgrade via the web portal)

Download `knowco2-ota-vX.Y.Z.zip` from the [latest release](../../releases/latest).

1. Open `http://knowco2-XXXX.local/update` in a browser on the same network.
2. Upload the OTA ZIP and wait for the device to apply and reboot.

### Option 3 — Source code

The full source is available as `Source code (zip)` on every release, or by cloning this repo. It is plain CircuitPython — no build step required.

### Installing CircuitPython (first time only)

The device needs the production-qualified CircuitPython runtime installed before
the firmware can run (currently **10.2.1**):

1. Use the board `.uf2` under `circuitpython-release/10.2.1/`, or download that
   exact version for the [Adafruit Feather ESP32-S3 Reverse TFT](https://circuitpython.org/board/adafruit_feather_esp32s3_reverse_tft/).
2. Double-tap the reset button — the Feather bootloader volume named
   `FTHRS3BOOT` appears.
3. Drag the `.uf2` onto that volume. The board reboots and the factory-default
   CircuitPython volume named `CIRCUITPY` appears.
4. Then follow **Option 1** above to install the firmware.

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

## Physical buttons

All customer instructions and on-device/web interfaces use the enclosure
labels **A**, **B**, and **C**:

| Physical control | Action |
|---|---|
| A | Toggle °C / °F |
| B | Cycle display mode (Text → Big CO₂ → Graph) |
| C (short press) | Toggle the measurement and network-information screens |
| C (hold about 2 seconds) | Switch between setup access-point and Wi-Fi client modes |
| B held during power-up/reset | Show the `KNOWCO2` service volume for maintenance |

For firmware contributors only, the internal board-pin mapping is A =
`board.D0`, B = `board.D1`, and C = `board.D2`. Do not expose those pin names
in customer-facing text.

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

Product-facing names are defined in [PRODUCT_NAMING.md](PRODUCT_NAMING.md).
