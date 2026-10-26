# Firmware behavioral tests

These run the REAL firmware modules on desktop CPython against a mocked
CircuitPython hardware stack (wifi, socketpool, ssl, displayio, miniqr,
espidf, microcontroller). No hardware needed; CI runs them on every PR.

| Suite | Covers |
|---|---|
| `test_cloud_heal.py` | Cloud TLS self-heal: pre-flight, teardown, retry-after-pause, bounded reset, web-activity grace, failure forensics |
| `test_ota_staged.py` | Signed OTA installation in a temporary filesystem: SHA-256 readback, invalid/tampered rejection before writes, version reservation, low-space per-file interruption/retry, replay/rollback and stale-module aliases. Never writes to host root |
| `test_snappy_ui.py` | UI responsiveness: single QR generation, matrix cache hits, deferred rebuild flag, pending-press capture, guarded label writes |
| `test_graph_perf.py` | Pixel-identical equivalence between the native bitmaptools graph path and the pure-Python fallback |
| `test_buttons.py` | Button taps, holds, combinations, busy-state polling, and wrap-safe timing |
| `test_api_v1_contract.py` | Platform-neutral Local API v1 validation, strict booleans, claim normalization, and secret-free payloads |
| `test_provisioning.py` | Write-only browser/app onboarding, AP/physical-write boundary, deferred STA handoff, fixed-origin activation, idempotent retries, and manufacturing/cloud identity separation |
| `test_product_naming.py` | Production brand/model names, physical A/B/C terminology, and the hidden/maintenance `KNOWCO2` service-volume contract |

Run every hardware-free suite from the repository root (Python 3.12 and OpenSSL):

```sh
python3 tools/run_tests.py
```

Each suite runs in an isolated subprocess. No root privileges, connected device,
network calls or release publication are required. The aggregate includes all
`tests/test_*.py` plus the mocked sensor and image-packaging root suites.
`test_ota_security.py`, `test_web_security.py` and `test_security_build.py` cover
signature/parser/NVM rejection, browser-origin/auth boundaries and fixture/release
isolation. Hardware qualification is tracked separately in `SECURITY_TESTING.md`.

Philosophy: every bug that reached hardware becomes a permanent test.
The mock stack exists because the alternative — discovering regressions
on a device with a 240x135 screen — costs hours per cycle.
