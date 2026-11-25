# CircuitPython 11 on the KnowCO2 Reverse TFT — experimental, USB only

Use one **Adafruit Feather ESP32-S3 Reverse TFT** with a CO₂ sensor and test credentials. This is an app build for a spare, not a production release or an OTA package. The production dependency lock in this checkout specifies 10.2.1; the runtime on an individual monitor must be read from the device.

## Install and compare on one board

1. Before changing anything, capture the exact runtime from `boot_out.txt`, TinyUF2 version and board ID from `INFO_UF2.TXT`, KnowCO2 HWID and hostname, host-visible USB serial, and current app version. Back up the complete service volume **privately**, including settings, and obtain the same working board-specific runtime and app for recovery. Use a spare approved for stock Adafruit UF2. For the stock 4 MB board, **TinyUF2 0.33.0 or later** is required; an already working compatible bootloader needs no update. Stop if the board/bootloader is different or unknown.
2. Use the extracted USB kit. The **contents** of `device/` belong at the root of the service volume; the outer ZIP and the UF2 do not. On the spare, hold **B** while pressing reset to expose `KNOWCO2` (a blank board uses `CIRCUITPY`). Interrupt the app with Ctrl-C in the maintenance console and enter `import supervisor; supervisor.runtime.autoreload = False` so partial copies cannot launch it. After making the backup, replace the old app-owned `knowco2/`, `lib/`, and `assets/` directories, instead of overlaying mixed `.py` and `.mpy` trees. Copy the kit's remaining files, with `code.py` last. Keep the board's settings. Eject and hard-reset normally. Never upload this ZIP to the web portal.
3. Run the baseline below on the board's **recorded working 10.x runtime** with this kit installed. For a fresh bench board the repository's pinned baseline is [10.2.1 UF2](https://downloads.circuitpython.org/bin/adafruit_feather_esp32s3_reverse_tft/en_US/adafruit-circuitpython-adafruit_feather_esp32s3_reverse_tft-en_US-10.2.1.uf2). Keep sensor, settings, brightness, radios and power conditions the same in both runs.
4. Double-tap RESET to mount `FTHRS3BOOT`. Copy only this [Adafruit Reverse TFT 11.0.0-beta.0 UF2](https://downloads.circuitpython.org/bin/adafruit_feather_esp32s3_reverse_tft/en_US/adafruit-circuitpython-adafruit_feather_esp32s3_reverse_tft-en_US-11.0.0-beta.0.uf2) there. SHA-256 of the downloaded UF2: `6aaa9016ef5fa7bb6b6518745e7d344a745cb539c534d96a356640f116f2fefe`. After reboot check `boot_out.txt` or KnowCO2's `/status` (`cp_version`) for the full beta tag, then repeat the same tests. Adafruit supports 10.x bundle libraries on this beta; both passes intentionally use identical application and library bytes.

## Measure the display

Hold **B** across reset, open the USB maintenance console, press Ctrl-C, then enter this **one line**:

```python
import supervisor; supervisor.set_next_code_file("/display_bench.py"); supervisor.reload()
```

This runs once as a program, so the beta's normal flash import behavior is measured. Running `import display_bench` in the REPL is not equivalent. Copy its four result lines off the console. Repeat three times per runtime (at the prompt, enter the same line); hard-reset without B to return to the normal app. The probe does not save or publish its synthetic readings.

| Compare | What to record |
| --- | --- |
| Text, Big CO₂ and full graph | For each view: p50, p95 and worst update + explicit TFT refresh time in µs, after five warm-up frames and over 64 changing readings. Use the median of three p95s per view. This measures drawing cost, not panel FPS or button latency. |
| Heap | Compare the post-GC UI heap printed by the probe, then compare the full app's `mem_free_min` at `/status` after the same network and screen sequence. In-place `.mpy` imports can save heap; neither bytes nor frame time measures battery life. |
| Real interactions | With the normal app, tap B through Text → Big CO₂ → Graph, check fresh readings, splash, layout at the 999/1000 boundary, graph updates and response during a Wi-Fi reconnect. A phone video of button-to-visible-change is useful. |
| No USB host | With the normal app on the graph, unplug USB **data** and use a battery or independent power for at least 60 minutes. Note start/end times, screen/button response and sample continuity. If measuring current, use equal brightness, radios, sample interval and supply for both runs. |

Keep individual runs. Investigate any freeze, missed sample, new allocation failure, or a p95 regression larger than normal run-to-run variation. Claim a speed or power improvement only when repeatable on this Feather. The new LVGL renderer in beta.0 is **P4/S31 only**; the S3 continues to use classic displayio. A separate S3 timer fix was tested upstream on MatrixPortal S3 with USB disconnected. The application uses the built-in `board.DISPLAY` and never calls the deprecated `displayio.release_displays()`.

## Compatibility and recovery gates

- Splash must appear after a cold boot on both runtimes. CircuitPython 11 accepts a **filename** for `OnDiskBitmap`; the old open-file argument silently skipped the KnowCO2 logo after logging an exception.
- The full beta tag should appear in diagnostics. The raw Espressif processor UID and USB serial change in 11; the KnowCO2 HWID and derived mDNS hostname should **match 10.x on the same board**. Check USB identity, existing cloud test-device association, TLS telemetry, and MQTT/Home Assistant discovery with no duplicate entity. Check the setup AP, read a sensor sample, save a non-secret setting and power-cycle. Record mismatches rather than pairing the unit again.
- Leave `CIRCUITPY_STORAGE_MAP_FILE` at its default for the main comparison. The beta can reject writes to imported `.mpy` files until reload. The updater in this base branch checks ZIP CRCs; it does **not** establish publisher authenticity. Qualify the separate signed updater before any OTA rollout: on an isolated test unit, stage a valid signed package including a currently imported module, reject a wrong-key or altered package before live files change, interrupt staging and confirm the last working app and settings recover. Keep USB recovery available. App-file updates do not replace the UF2; do not assume an A/B runtime fallback.
- **Rollback drill:** double-tap RESET, reinstall the original working board-specific UF2 on `FTHRS3BOOT`, restore the original app-owned directories, root files and private settings from backup in B maintenance mode, and remove the bench script. Eject, hard-reset and verify original runtime, sensor, identity and telemetry. A failure here blocks promotion regardless of timing results.

## Rebuild the USB kit

From a clean checkout, use the repository's pinned CircuitPython 10.2.1 `mpy-cross` (same compiler for the A/B test):

```sh
python3 bench/circuitpython11/build_usb_kit.py --mpy-cross /path/to/mpy-cross --output ../KnowCO2-CP11-beta0-USB-bench.zip
```

`BUILD.txt` in the ZIP records the commit and compiler. No production lock, firmware release tag, flasher profile, bootloader or OTA feed is changed by building the kit.

Upstream: [beta release notes](https://github.com/adafruit/circuitpython/releases/tag/11.0.0-beta.0) · [S3 USB-disconnected display fix](https://github.com/adafruit/circuitpython/pull/11907) · [UID change](https://github.com/adafruit/circuitpython/pull/11494) · [flash imports and write protection](https://github.com/adafruit/circuitpython/pull/11894) · [Reverse TFT board and bootloader requirements](https://circuitpython.org/board/adafruit_feather_esp32s3_reverse_tft/).
