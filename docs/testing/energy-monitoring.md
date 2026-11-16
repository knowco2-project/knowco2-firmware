# Energy monitoring: isolated engineering experiment

**Not release-qualified. Disabled by default. No production power coefficients.**

Base: firmware main `18a8b9e42da34d49495f8a408380b4f1a197fac5`, including the
merged USB receive/identity fix. This does not merge the security, sensor or
virtual-input draft branches, and does not alter the dependency lock or releases.

## What is implemented

- A constant-size integer accumulator with a 36-byte numeric array; Python,
  module, profile, and telemetry heap are additional and require device testing.
- An optional observer in the existing loop. It observes cached state, not a
  second sensor read or a second CO2 history. It never changes sampling,
  brightness, sleep, battery, calibration, watchdog or upload policy.
- One non-retained JSON topic appended after CO2/temp/humidity in the existing
  MQTT connection, at the existing cadence. No additional MQTT/HTTPS connection.
- Explicit unknown intervals, mode/profile checks, wrap-safe clocks, fatal clock
  and saturation flags, and average modeled power over completed >=60 s windows.
- Experimental Home Assistant YAML for wall power/energy and a user-entered
  electricity rate with a 30-day cost PROJECTION. Not automatic energy discovery
  and not a change to the separate KnowCO2 HACS integration.

An observer exception disables it for the boot. CO2 work still runs; optional
telemetry failures do not prevent the original MQTT readings being published.
The existing MQTT transport's per-topic failure handling is unchanged; QoS 0
and network outages are not magically lossless.

## Deliberate limits

There is no current/shunt meter in this feature. MAX17048 voltage/SOC is not
wattage. No numeric estimate is published without an engineering profile. A
profile supplied by the operator is still `experimental_model`, not certified
measurement or proof of accuracy.

This first slice supports externally powered, physically verified **no-battery**
bench configurations. It does NOT implement battery runtime, charging accounting,
power-source detection, battery-reader hardening, persistent energy counters,
cloud API fields, screen UI, USB API fields or a retail settings interface.
The battery/reset/cache findings from the earlier design remain separate work.

Power source is **operator-declared**, not inferred from USB enumeration. False
USB enumeration does not prove battery use. Installing a battery, changing an
adapter, changing firmware/libraries, adding accessories, or changing the
physical sensor invalidates the corresponding measurement profile. Stop/reboot
and requalify before using the totals. The exact physical SCD variant and driver
must be verified independently, including known pending sensor fixes.

Startup before observer initialization and the unreported tail before abrupt
power loss are not captured. Reboot resets the RAM total. Reconnect within the
same boot can recover covered energy through the cumulative counter; a reboot
while offline cannot. Unknown operating states/long gaps are counted as
unmodeled time, NEVER filled in as 0 W. Thus energy is tracked/model-covered
energy, not guaranteed lifetime or billed consumption.

## Why wall power is different

`wall_ac` means a reference measurement at the wall INCLUDING the specific USB
adapter's losses and idle draw. `usb_dc` means power into the device after the
adapter. Keep those boundaries separate. No guessed efficiency multiplier is
applied. A USB-only profile cannot support an accurate electricity-bill claim.

A low-load integrating reference meter must resolve this device's actual load;
a plug that displays zero at low power is not evidence of zero consumption.
Capture reference uncertainty, measurement duration, adapter identity, voltage,
board revision, sensor and exact firmware/MPY/library manifests. Validate against
independent runs rather than only the runs used to fit the model.

## Safe activation on an engineering device

1. Wait for the branch's CodeBuild checks and review its diff. Use a spare device.
   Back up identity, settings and credentials; manufacturing erase can erase them.
   Do not flash a customer unit or use an unqualified scoped OTA artifact.
2. Build the source from this branch with the flasher's explicit local-source
   workflow and record the exact toolchain/dependency tuple. This branch does not
   assert that a new CircuitPython or bundle has been qualified.
3. Locally add `"energy_monitor_enabled": true` to the device's settings JSON and
   restart. Do not replace the entire settings file or expose credentials. The
   shipped `knowco2/power_profile.py` has `PROFILE = None`: observation works but
   watts and energy remain unavailable. No fabricated sample values are installed.
4. Configure the existing authenticated/TLS MQTT connection normally. Observe
   `<mqtt_topic_prefix>/<lowercase_hwid>/energy`. This can expose the `mode` key
   and unmodeled time for calibration without any extra network schedule.
5. After collecting reference measurements, supply a local profile in
   `knowco2/power_profile.py`, rebuild, and repeat the physical comparison.
6. Disable by removing/setting the local opt-in to false and rebooting. Restore
   the known-good full build if needed. There is no changed flash-counter schema.

Profile schema (replace placeholders; do NOT enter guessed wattage):

```python
PROFILE = {
    "id": "unique-measurement-profile-id",
    "reference": "measurement-record-id",
    "adapter": "tested-adapter-model-and-unit",
    "boundary": "wall_ac",  # or usb_dc, never interchangeable
    "battery_absent_confirmed": True,
    "build_id": "energy-experiment-v1",
    "board_id": "exact state.board_id_str",
    "sensor_model": "verified sensor model",
    "firmware": "exact version.FIRMWARE_VERSION",
    "circuitpython": "exact version.CP_VERSION",
    "modes": {
        # tuple(mode_from_telemetry): measured_integer_average_milliwatts
        # Up to 32 bounded rows. No wildcard or fallback coefficient.
    },
}
```

The mode tuple is `(low_power, effective_sensor_period_ms, brightness_permille,
wifi_mode, radio_connected, display_mode, screen, activity)`. Activities include
`idle`, `cloud`, `mqtt`, `aio`, `ntp`, `pair`, `wifi`, `sensor_recovery`,
`mode_change`, and `display_change`. Missing rows are unknown. A mode/HTTP change
without an exact timestamp discards the ambiguous interval. Calibrate typical
CPU/UI work into each operating state's average; unsupported fault workloads
must not be advertised as measured. Sensor faults and profile mismatches suppress
estimation. There is no attempt to integrate individual sensor current pulses.

State period is normally 1 second; bracketing known blocking calls uses actual
transition times. There is no sample list. Each `power_mw_est` is a completed
modeled-time window average, not the power at the instant MQTT happens to send.
Unknown intervals clear that average until another complete valid window exists.

## Home Assistant and costs

Use `docs/examples/home-assistant-energy.yaml` as a manually reviewed HA package.
Replace HWID, prefix and generated entity IDs. It supports the default 60 s MQTT
and 300 s LP cadence with 900 s expiry; adjust for other cadences. No retained
state payload is used, so a broker restart cannot resurrect an old reading as
fresh. Configured entities become unavailable after expiry or invalid data.

The energy entity uses `state_class: total` plus an explicit per-boot UTC
`last_reset`, rather than relying on a decrease to detect resets. This handles a
new boot whose first received total exceeds the previous boot's last total.
Until NTP gives a plausible timestamp it is unavailable. Correct UTC and normal
boot ordering must be checked on hardware; the timestamp is not billing evidence.
Use a new HA unique ID when changing the physical measurement boundary/profile,
and do not manually zero an active counter without starting a new session.

Add the energy entity under **Individual devices**, not as whole-home grid
consumption. Do not add the same device's external meter and model as independent
loads: they represent the same consumption.

Enter the VARIABLE per-kWh supply + delivery charges and applicable variable
charges from the user's own tariff in the helper. Set the confirmation toggle
only after entering the rate. Currency defaults to USD in the example; change
both helper and sensor units together for another currency. Fixed monthly
customer charges are not caused by continuously running this device and are
excluded. No local rate or tariff has been assumed.

`projected_cost = recent_average_W / 1000 * 24 * 30 * currency_per_kWh`

This is a projection for unchanged operating conditions/rate, NOT historical
billed cost. Illustrative arithmetic only: 0.4 W at 0.30 USD/kWh is 0.288 kWh or
0.0864 USD over 30 days. Those are NOT measurements or a tariff for KnowCO2.
For historical time-of-use costs, integrate observed energy deltas against the
rate in effect during each interval on HA/cloud. Multiplying all past kWh by
TODAY'S rate would reprice history incorrectly. That ledger is not implemented
here. HA's grid-price setting should not be represented as automatic per-device
cost accounting; the example creates an explicit projection sensor.

## Qualification checklist (not completed by host tests)

- Existing CodeBuild tests plus new energy suites pass on the exact commit.
- Compare observer off / enabled-unconfigured / enabled-calibrated on the board.
  Record `gc.mem_free()` after GC, minimum heap during TLS, largest IDF block,
  loop latency, button responsiveness, CO2 sample gaps, resets and error counts.
- Initial targets, NOT measurements: <=8 KiB added retained heap, <=12 KiB peak
  additional heap and <=12 KiB added compiled application footprint. Measure the
  packaged image and OTA staging/free-space reserve. Reject or simplify if over.
- Verify no added periodic flash writes and no increased sensor-read/upload
  cadence. Run at least 72 hours of mixed CO2, display, web and network workloads.
- Compare integrated wall energy with an adequate reference meter for normal,
  dim, LP, AP/STA, successful/failed uploads, reconnects and safe fault injection.
  Initial engineering target: <=10% integrated-energy error only on declared
  supported cases; no accuracy claim until evidence supports it.
- MQTT/HA: no NTP, startup, reboot with higher first total, HA restart, broker
  restart, duplicates, packet loss, unavailable power, unknown intervals and
  changed profiles. Real HA validation has not been performed by these tests.
- Flat-rate helper: unknown/zero/change-of-rate behavior; no historical-cost claim.
- After integration with sensor/security branches, rebuild and repeat qualification.

Local tests: `python3 tests/test_energy.py -v`; then from `tests/`,
`python3 test_energy_mqtt.py -v` and `python3 test_mqtt_tls.py -v`.
Only actual tested outcomes belong in the PR verification section. Arithmetic,
fixed storage and mocked transport tests are not hardware electrical validation.

## Primary references

- https://www.home-assistant.io/integrations/sensor.mqtt/
- https://www.home-assistant.io/integrations/sensor/
- https://www.home-assistant.io/docs/energy/individual-devices/
- https://www.home-assistant.io/docs/energy/faq/
- https://docs.circuitpython.org/en/stable/shared-bindings/supervisor/
- https://learn.adafruit.com/esp32-s3-reverse-tft-feather/power-management
