# Firmware behavioral tests

These run the REAL firmware modules on desktop CPython against a mocked
CircuitPython hardware stack (wifi, socketpool, ssl, displayio, miniqr,
espidf, microcontroller). No hardware needed; CI runs them on every PR.

| Suite | Covers |
|---|---|
| `../test_sensors.py` | Sensor registry, SCD40/SCD41/SCD43 identity, low-power switching, calibration dispatch and SCD30 fallback |
| `test_scd4x_compatibility.py` | Pinned Adafruit 1.4.11 and 1.4.13 source on a simulated I2C bus: command timing, CRC, identity, measurements, calibration, recovery and public-API use; **not hardware verification** |
| `test_firmware_dependencies.py` | Effective library tree, component release pins, source/MPY drift rejection, manifests and complete-image copying |
| `test_cloud_heal.py` | Cloud TLS self-heal: pre-flight, teardown, retry-after-pause, bounded reset, web-activity grace, failure forensics |
| `test_ota_staged.py` | Transactional OTA: staging, CRC verification, corrupt/truncated rejection, low-space per-file mode, junk cleanup. **Writes to the container root filesystem — run inside a disposable container as root**, exactly like CI does |
| `test_snappy_ui.py` | UI responsiveness: single QR generation, matrix cache hits, deferred rebuild flag, pending-press capture, guarded label writes |
| `test_graph_perf.py` | Pixel-identical equivalence between the native bitmaptools graph path and the pure-Python fallback |
| `test_buttons.py` | Button taps, holds, combinations, busy-state polling, and wrap-safe timing |
| `test_api_v1_contract.py` | Platform-neutral Local API v1 validation, strict booleans, claim normalization, and secret-free payloads |
| `test_provisioning.py` | Write-only browser/app onboarding, AP/physical-write boundary, deferred STA handoff, fixed-origin activation, idempotent retries, and manufacturing/cloud identity separation |
| `test_product_naming.py` | Production brand/model names, physical A/B/C terminology, and the hidden/maintenance `KNOWCO2` service-volume contract |

Run locally from `tests/`:

```sh
docker run --rm -v "$PWD/..:/fw:ro" -w /fw/tests python:3.12-slim-bookworm \
  sh -c "python test_cloud_heal.py && python test_snappy_ui.py && python test_graph_perf.py && python test_buttons.py && python test_web_onboarding.py && python -m unittest test_api_v1_contract.py && python test_provisioning.py && python test_product_naming.py && python test_ota_staged.py"
```

Philosophy: every bug that reached hardware becomes a permanent test.
The mock stack exists because the alternative — discovering regressions
on a device with a 240x135 screen — costs hours per cycle.

Run the focused SCD43 compatibility checks from the repository root:

```sh
python3 test_sensors.py
python3 -m unittest discover -s tests -p test_scd4x_compatibility.py -v
```

The offline vendor source fixture and its license/provenance are in
[`fixtures/`](fixtures/README.md). The two-unit physical validation procedure is
[`docs/scd43-validation.md`](../docs/scd43-validation.md).
