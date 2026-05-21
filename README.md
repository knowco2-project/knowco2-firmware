# Firmware — CircuitPython

Firmware license: [MIT](LICENSE)

## Overview

The knowco2 firmware runs on [CircuitPython](https://circuitpython.org) on the Adafruit Feather ESP32-S3 Reverse TFT. It is a single `.py` file (`code.py`) plus a `lib/` folder of dependencies.

## Files

| File | Description |
|------|-------------|
| `code.py` | Main firmware source |
| `boot.py` | Boot configuration (sets up USB drive mode) |
| `lib/` | CircuitPython library dependencies |

Pre-built releases (ready to drop onto the device) are available at:
https://github.com/knowco2-project/firmware-releases/releases

## Dependencies

All dependencies are standard CircuitPython libraries available from the [Adafruit CircuitPython Bundle](https://circuitpython.org/libraries).

Key libraries used:
- `adafruit_scd4x` — SCD40/SCD41 driver
- `adafruit_scd30` — SCD30 driver
- `adafruit_st7789` — TFT display driver
- `adafruit_display_text` — Text rendering
- `adafruit_minimqtt` — MQTT client
- `adafruit_ntp` — NTP time sync
- `adafruit_httpserver` — Local web portal

## Flashing

**Via USB (first time or recovery)**

1. Download the latest CircuitPython `.uf2` from https://circuitpython.org/board/adafruit_feather_esp32s3_reverse_tft/
2. Double-tap the reset button on the Feather to enter the UF2 bootloader (`FTHRS3BOOT` drive appears)
3. Drag the `.uf2` file onto the drive — the board reboots automatically
4. The `CIRCUITPY` drive appears — copy `code.py` and `lib/` onto it

**Via OTA (existing devices)**

Navigate to `http://knowco2-XXXX.local/update` and upload the new `code.py`.

**Via USB (existing devices)**

Hold the middle button (B) while powering on and keep holding until the screen blinks. The `CIRCUITPY` drive mounts and you can replace files directly.

## License notice

SPDX-License-Identifier: MIT
