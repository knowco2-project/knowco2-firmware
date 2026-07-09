# Firmware behavioral tests

These run the REAL firmware modules on desktop CPython against a mocked
CircuitPython hardware stack (wifi, socketpool, ssl, displayio, miniqr,
espidf, microcontroller). No hardware needed; CI runs them on every PR.

| Suite | Covers |
|---|---|
| `test_cloud_heal.py` | Cloud TLS self-heal: pre-flight, teardown, retry-after-pause, bounded reset, web-activity grace, failure forensics |
| `test_ota_staged.py` | Transactional OTA: staging, CRC verification, corrupt/truncated rejection, low-space per-file mode, junk cleanup. **Writes to the container root filesystem — run inside a disposable container as root**, exactly like CI does |
| `test_snappy_ui.py` | UI responsiveness: single QR generation, matrix cache hits, deferred rebuild flag, pending-press capture, guarded label writes |
| `test_graph_perf.py` | Pixel-identical equivalence between the native bitmaptools graph path and the pure-Python fallback |

Run locally: `docker run --rm -v $PWD/..:/fw -w /fw/tests python:3.12 sh -c "python test_cloud_heal.py && python test_snappy_ui.py && python test_graph_perf.py"`

Philosophy: every bug that reached hardware becomes a permanent test.
The mock stack exists because the alternative — discovering regressions
on a device with a 240x135 screen — costs hours per cycle.
