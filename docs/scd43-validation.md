# SCD43 compatibility assessment and two-unit validation

Assessment date: 2026-11-10. Baseline: firmware `main` at
`aa7533ca40be5fa352a77f2f550dfbd0dfdd2e7e`.

**Decision: suitable for an engineering evaluation, not yet qualified for the
product. Neither physical SCD43 unit has been tested in this investigation.**
No firmware has been flashed, deployed, tagged or released. Installation of
this candidate on a device requires Mike's approval. The separate security
candidate in PR #22 has not been merged into this branch.

## What the evidence establishes

| Evidence | Result | Limit |
|---|---|---|
| Sensirion command specifications | SCD43 shares the periodic measurement, calibration and recovery interface used here | Protocol compatibility is not electrical, enclosure or accuracy qualification |
| Locked Adafruit bundle | Uses SCD4X 1.4.11; exact upstream source pinned by bundle submodule | Host tests execute source, not the installed CircuitPython `.mpy` |
| Existing main implementation | Incorrect identity heuristic, low-power and reset API names; idle-setting and FRC lifecycle problems | These affect SCD40/SCD41 too; they are not evidence that SCD43 hardware is defective |
| Candidate software | 20 focused compatibility tests and eight registry scenarios pass locally | Simulated I2C replies, time and failures; no measured ppm or device stability data |
| Unit A / Unit B | **NOT TESTED / NOT TESTED** | Physical evidence is required before any support or product claim |

Source provenance and hashes are in [tests/fixtures/README.md](../tests/fixtures/README.md).
The production dependency lock and all vendor `.mpy` files are unchanged.

## Command and capability comparison

The following shared interface comes from Sensirion's [SCD4x datasheet v1.7,
April 2025, sections 3.5-3.11][datasheet]. Numbers are command codes, not I2C
addresses. All three parts use address `0x62` and per-word CRC.

| Function | SCD40 | SCD41 | SCD43 | Command / timing |
|---|---|---|---|---|
| Periodic CO2/T/RH | Yes | Yes | Yes | `0x21B1`, 5 s |
| Low-power periodic | Yes | Yes | Yes | `0x21AC`, 30 s |
| Ready / read | Same | Same | Same | `0xE4B8` / `0xEC05`, 1 ms |
| Stop | Same | Same | Same | `0x3F86`, 500 ms |
| Variant | 0 | 1 | 5 | `0x202F`, upper nibble, 1 ms, idle |
| Serial | Same | Same | Same | `0x3682`, 1 ms, idle |
| ASC enable / read | Same | Same | Same | `0x2416` / `0x2313`, idle |
| Forced calibration | Same | Same | Same | `0x362F`, idle |
| Altitude / temperature offset | Same | Same | Same | `0x2427` / `0x241D`, idle |
| Ambient pressure | Same | Same | Same | `0xE000`, allowed while measuring |
| Reinitialize / self-test | Same | Same | Same | `0x3646` / `0x3639`, idle |
| Single-shot CO2/T/RH | No | Yes | Yes | `0x219D`, 5 s |

SCD43's specified range is 400-5000 ppm and stated CO2 accuracy is
±(30 ppm + 3% of reading), subject to the manufacturer's test conditions.
Its output range is wider than its specified accuracy range. See the
[SCD43 product specification][product]; the existing graph limits are display
choices, not proof of sensor accuracy. KnowCO2 uses periodic modes, not
single-shot, power-down/wake-up or adjustable ASC periods. Those features
remain outside this qualification.

## Defects reproduced and correction

The [actual pinned Adafruit source][driver] exposes `measure_single_shot` on
every instance of the same class, regardless of which sensor is connected.
Main therefore labels an SCD43 (and an SCD40) as SCD41 with this driver.

| Area | Main behavior | Candidate behavior |
|---|---|---|
| Identity | Infers SCD41 from a method's existence | Uses the public variant property if available; otherwise sends the documented command using the locked driver's transport and CRC check |
| Identity unavailable | Guesses a model | Keeps `SCD4x`; valid serial and periodic start still required. Unknown nibble, NACK and bad CRC never produce an SCD43 claim |
| Low power | Checks/calls nonexistent `start_low_power_periodic_measurement` | Calls `start_low_periodic_measurement`; supports all three variants |
| Recovery | Calls nonexistent `soft_reset`; the generic wrapper suppresses the error | Uses `reinit`, waits conservatively, restores settings applied through the adapter, restarts the requested mode; failure propagates |
| ASC/altitude | Sends idle-only settings after detection already started measurements | Stops, writes, then resumes the previous mode |
| FRC failure | Pinned driver unpacks signed data but compares against unsigned `0xFFFF`; failure can look successful | Checks the raw failure sentinel for the legacy `None` return; accepts valid signed corrections from newer APIs |
| FRC restart | Vendor method leaves measurements stopped | Resumes the previous mode after success or failure; failed restart returns failure |

The two uses of vendor private internals (legacy variant query and legacy FRC
reply check) are deliberate, scoped to the locked driver's behavior and covered
by tests. A future bundle update must rerun these tests and hardware checks.
No factory reset or automatic EEPROM persistence is added.

Remaining limits: the shared settings API still treats altitude/pressure `0` as
no-op; it cannot reliably clear an already-applied compensation value live.
Do not interpret the web page's generic settings toast as readback or proof of
FRC success. Save/apply nonzero compensation separately **before** FRC, and use
serial evidence below. This PR does not redesign calibration UI, enforce the
operator's stable-reference prerequisite, or qualify the separate security/OTA
candidate. Recovery restores only settings applied through this adapter.

## Bench setup and evidence record

1. Label the received sensors **A** and **B**. Photograph the part/board markings,
   record supplier, board revision and serial, and verify the breakout's supply
   and I2C voltage requirements before wiring. Power off before changing wiring.
2. Use one sensor per controller/bus. Two `0x62` sensors cannot share one bus
   directly. Prefer two otherwise identical KnowCO2 engineering units so they
   can sample side by side. If only one is available, test sequentially and
   record that the air conditions were not simultaneous.
3. Record actual firmware commit, CircuitPython version, driver version and
   library-set ID for each controller. Do not assume an existing device has the
   repository's bundle. Back up its source/configuration and identity privately
   before any approved installation; do not put credentials in the test log.
4. Keep both sensors and a reference in the same well-mixed air, away from breath,
   direct sunlight, heat sources, vents and condensation. Use the intended
   production supply and cable. Keep a working SCD41 as a regression comparison;
   it is not a calibration reference unless its calibration is documented.
5. Preserve as-received calibration first. Do not start with FRC, factory reset,
   `persist_settings`, or the existing `sensor_smoketest.py`: that script changes
   ASC and switches mode automatically. Obtain baseline data before changing
   calibration state.

Create a log with these fields for every run:

```text
date/time,unit,controller,firmware_commit,circuitpython,driver,library_set,
sensor_model,sensor_serial,mode,elapsed_s,fresh_sample_number,co2_ppm,temp_C,
rh_percent,reference_ppm,reference_uncertainty,asc,altitude_m,pressure_hPa,
crc_or_i2c_errors,recovery_events,resets,operator_notes
```

## Approved-device measurement procedure

This is a future operator procedure, not a record of work performed. Approve the
exact candidate commit and intended engineering device before copying any files.
Use the existing service workflow; do not use a manufacturing erase or overwrite
production identities. If installing source over a compiled image, remove the
corresponding stale adapter `.mpy` under the approved install plan; confirm the
executed commit. Qualify the final combined security/sensor image separately.

After approved installation, stop the application with Ctrl-C in a serial REPL.
Use paste mode for the following block. Capture serial output on the computer,
not by writing a continuous log to device flash:

```python
import board, os, time, adafruit_scd4x
from knowco2.sensors.scd4x import SCD4xSensor
print(os.uname())
print("driver", getattr(adafruit_scd4x, "__version__", "unknown"))
s = SCD4xSensor.detect(board.I2C())
assert s is not None
print("IDENTITY", s.model, s.read_serial(), s.supports_low_power)

def sample_for(seconds):
    started = time.monotonic()
    samples = 0
    errors = 0
    print("elapsed_s,sample,co2_ppm,temp_C,rh_percent")
    while time.monotonic() - started < seconds:
        try:
            if s.data_ready:
                co2, temp, rh = s.read()
                samples += 1
                print("%.3f,%d,%s,%.3f,%.3f" %
                      (time.monotonic() - started, samples, co2, temp, rh))
        except Exception as error:
            errors += 1
            print("ERROR", time.monotonic() - started, repr(error))
        time.sleep(0.25)
    print("TOTAL", samples, "ERRORS", errors)
```

Run each phase for **both A and B**, retaining raw logs:

| Phase | Operator action | Evidence / proposed engineering gate |
|---|---|---|
| Identity | Read the startup line; repeat after ten cold power cycles and several soft reloads | `SCD43`, stable unique serial for each unit, low-power true; any generic label blocks identity acceptance |
| Normal readings | `s.set_low_power(False)` then `sample_for(1800)` | Fresh CO2, finite T/RH and plausible values; nominal 5-second cadence, no CRC/I2C errors |
| Controlled air change | Change ordinary room ventilation/occupancy, then return to baseline | Both units and reference respond in the same direction; compare after stabilization, not during the transient. Do not breathe directly on sensors |
| Low power | `s.set_low_power(True)` then `sample_for(1800)` | Nominal 30-second fresh cadence; no stale cached samples counted as fresh |
| Return to normal | `s.set_low_power(False)` then `sample_for(600)` | Five-second cadence resumes |
| Recovery | In each mode call `s.recover(low_power=False)` or `s.recover(low_power=True)` as appropriate, then sample | New samples resume, mode and applied ASC/compensation retained, serial unchanged, no factory reset |
| Extended product run | Reload full approved firmware; run 24 h normal plus 4 h low-power on each unit with display and intended networking active | No unexplained sensor resets, CRC/I2C failures, lockups, stuck samples or false frozen banners. Record heap/uptime and recovery count if available |

Suggested bench-only timing gates, not manufacturer promises: first fresh result
within 10 seconds normal / 35 seconds low-power after starting; at least 99% of
expected samples over a steady logging segment; investigate any steady-state gap
over 15 / 75 seconds respectively. The full application polls differently from
this REPL logger, so record its scheduling separately. Identical consecutive ppm
values are possible; freshness is determined by `data_ready`, not value changes.
The normal 30-second and low-power 75-second application recovery thresholds
must not produce recovery loops during the soak.

Swap A/B between controllers after the first comparison if a bias or instability
appears. A fault that follows the controller suggests supply/wiring/integration;
a fault that follows the sensor warrants sensor investigation. Log the swap.
Do not hot-unplug a powered sensor to simulate a fault. Simulated bus failures
are covered on the host; physical fault injection needs a controlled bench setup.
If reinitialization cannot restore operation, power-cycle the sensor supply,
not just the MCU. Confirm the board actually removes sensor power on that cycle.

## Accuracy and calibration checks

**Baseline first.** At stable outdoor-like, ordinary indoor and higher indoor CO2
levels achievable safely, collect at least 10 minutes of settled observations
after allowing thermal/air stabilization (start with 15-30 minutes). Record mean,
standard deviation, min/max, A-minus-reference, B-minus-reference and A-minus-B.
Compare like-for-like sampling modes. Enclosure temperature and humidity offsets
need their own evaluation; room wall-thermostat readings are not co-located truth.

With a traceably calibrated reference, calculate an engineering comparison band
from SCD43's stated error plus reference uncertainty and a justified allowance
for spatial/time mismatch. For example, the sensor term at 1000 ppm is 60 ppm;
it is not the entire system comparison allowance. A/B agreement alone cannot
establish absolute accuracy. Without a suitable reference, mark accuracy/FRC
validation **inconclusive**, even if trends look good. Test the claimed upper
range only with suitable controlled equipment; do not raise occupied-room CO2
to 5000 ppm for this exercise.

**ASC (automatic self-calibration).** Record the as-found state while idle:

```python
s.device.stop_periodic_measurement()
print("ASC readback", s.device.self_calibration_enabled)
s.set_low_power(False)
```

After preserving the baseline, use `s.set_asc(False)`, stop/read back as above,
and restore the desired mode. Repeat with `True`, then test recovery and read
back again. The candidate restores the runtime setting after `reinit`; it does
not persist it to sensor EEPROM. Check full-application reboot behavior against
the saved KnowCO2 setting separately. Do not assume direct REPL changes survive
power loss or change the application's saved configuration.

ASC behavior requires a longer test: its default initial/standard periods are
44/156 accumulated hours, with uninterrupted operating blocks of at least four
hours and periodic exposure to the assumed background concentration. Plan a
7-14 day logged follow-up covering an initial and subsequent correction period;
24-hour stability does not validate ASC. Do not assume the factory 400 ppm ASC
target equals actual outdoor air. Maintain documented reference exposure and
record drift before/after; do not shorten the periods simply to get a quick pass.
See [Sensirion's API guidance][sensirion-api] for the operating assumptions.

**FRC (forced recalibration) changes calibration history.** Do this only after
baseline testing, with a stable measured reference, initially on A so B remains
an as-received comparison. Apply actual pressure/altitude compensation separately
first. Run in the intended mode and supply voltage for at least three minutes
in uniform, constant reference air. Then:

```python
# Replace this placeholder with the measured reference, not a guessed 400/420.
reference_ppm = YOUR_MEASURED_REFERENCE_PPM
ok = s.force_calibration(reference_ppm)
print("FRC accepted and measurement restarted:", ok)
sample_for(600)
```

The driver handles stop/wait and FRC timing. A raw `0xFFFF` means failure;
successful signed corrections can be positive, zero or negative. Record the
boolean, logs, pre/post bias and resumed cadence. Recheck after a full power
cycle and after recovery. Repeat on B only after reviewing A's result and the
effect of the intentional calibration change. Never use factory reset as a
routine recovery test: it erases field-calibration history. See
[Sensirion's FRC/recovery interface guidance][sensirion-embedded].

## Acceptance record

| Gate | Unit A | Unit B |
|---|---|---|
| Board / sensor serial / controller / exact software recorded | NOT TESTED | NOT TESTED |
| Explicit identity after cold and soft restarts | NOT TESTED | NOT TESTED |
| Normal / low-power / return-to-normal readings | NOT TESTED | NOT TESTED |
| Reference comparison and uncertainty assessed | NOT TESTED | NOT TESTED |
| Recovery and configuration readback | NOT TESTED | NOT TESTED |
| 24 h normal + 4 h low-power product soak | NOT TESTED | NOT TESTED |
| ASC readback and extended behavior | NOT TESTED | NOT TESTED |
| FRC accepted, measured improvement and persistence | NOT TESTED | NOT TESTED |

Archive raw logs, reference details, anomalies and the decision for each unit.
Pass both units before calling this firmware/hardware combination compatible;
then qualify the intended enclosure, final software/security integration and
manufacturing process. Two evaluation units do not establish production yield,
long-term reliability or a finished-product certification claim.

[datasheet]: https://sensirion.com/media/documents/48C4B7FB/67FE0194/CD_DS_SCD4x_Datasheet_D1.pdf
[product]: https://sensirion.com/products/catalog/SCD43
[driver]: https://github.com/adafruit/Adafruit_CircuitPython_SCD4X/blob/cb3c7f1186c5ceba466de9d11404808d3db06691/adafruit_scd4x.py
[sensirion-api]: https://sensirion.github.io/python-i2c-scd4x/api.html
[sensirion-embedded]: https://github.com/Sensirion/embedded-i2c-scd4x/blob/master/scd4x_i2c.h
