# KnowCO2 Firmware — Maintainability Guide

The goal: a codebase that a stranger can read on a Saturday, modify with
confidence, and verify without owning hardware. The device's hackability
IS the product — the code has to live up to that.

## Decisions already made (and why)

**Ship .py, not .mpy.** Precompiling to `.mpy` would roughly halve file
sizes and speed imports/boot — but a user opening the `KNOWCO2` service
volume and finding opaque binaries kills the "edit your own monitor" story that
differentiates KnowCO2 from every sealed $50 competitor. Performance is
won in hot paths instead (see below). If flash pressure ever becomes
critical, offer a second "compact" release artifact built by CI —
never make .mpy the only option.

**Hot paths use native modules, not clever Python.** The graph went from
~25,000 interpreted pixel writes to ~77 `bitmaptools.fill_region` calls
(RC-51) — a 3-line-diff-per-site change that keeps the drawing logic
readable. The pattern: when Python-level loops touch pixels, bytes, or
sockets per-element, look for the CircuitPython native module first
(`bitmaptools`, `bitmaptools.draw_line`, `struct`, `binascii`)
before restructuring anything.

**Slow work never runs in a button handler.** Handlers flip flags
(`qr_refresh_needed`, `graph_refresh_needed`); the main loop executes
deferred work when idle; caches make repeat work free; `poll_buttons()`
inside anything slow latches presses as pending. Any new feature that
draws, computes, or networks should follow this shape.

**Every hardware bug becomes a desktop test.** The `tests/` suites run
the real firmware modules under a mocked CircuitPython stack. The rule:
when a bug is fixed, the failing scenario is added before the fix ships.
This is how a one-person project keeps five subsystems from regressing
each other.

**Releases are RC-versioned, named for their theme** (`RC-51-Perf-v1`),
delivered as OTA zips with SHA-256, and repo changes land via
SHA-guarded all-or-nothing patch scripts validated from every plausible
baseline tree.

## Recommended next steps, in order

1. **Ship V1 first.** The architecture below is for after revenue, not
   before it. Nothing here blocks shipping.

2. **Split `code.py` into loop tasks (V1.x).** The main loop is ~800
   lines interleaving buttons, sensor, cloud, MQTT, NTP, dimming, memory
   telemetry. Refactor into small task functions with a cadence table:
   ```python
   TASKS = [  # (interval_s, fn) — the loop stays 20 lines forever
       (0.0,  buttons.task),
       (1.0,  sensor.task),
       (None, cloud.task),      # self-scheduling (backoff)
       (2.0,  battery.task),
       ...
   ]
   ```
   Pure mechanical extraction — no behavior change — done one task per
   PR so each diff is reviewable and device-testable in isolation.

3. **Lazy i18n (V1.x, also a RAM/flash win).** The 22 language modules
   are the largest chunk of the tree. Import only the active language;
   build the portal's translation blob on demand and cache it. Frees
   flash headroom (see OTA low-space mode) and trims import-time RAM.

4. **Extract a `knowco2` PyPI library (Works-With-HA prerequisite).**
   The HMAC signing, /status schema, and pairing logic are already
   cleanly separated — publishing them is mostly packaging work, and
   the HA core PR requires it.

5. **Keep the Rust port a community V2 project.** Performance is no
   longer the argument for it (the hot paths are native now); Matter/
   Thread support is. Revisit when that matters commercially.

## Code style, briefly

Comments explain WHY (the RC-48 OTA installer is the model: every
non-obvious block carries its failure story). Public functions get
docstrings; internal helpers get one honest line. No module imports the
UI layer except `code.py` and `knowco2/ui/` — the `runtime` hook module
exists precisely so telemetry/web never touch displayio. Guarded
imports (`try/except ImportError`) for every CircuitPython-only module
keep the whole tree importable on desktop CPython — that property is
what makes the test suite possible; never break it.
