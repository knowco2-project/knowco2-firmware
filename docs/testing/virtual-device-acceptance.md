# Virtual-device acceptance: first implementation

Status: **input-conformance foundation**, not a completed virtual device.
The browser must eventually operate the real KnowCO2 model and see output from
candidate firmware, not a JavaScript rewrite of the product. This first change
builds and tests the input/time/power boundary that such a browser will use.

## Implemented

`tests/virtual_device/model.py` executes the checkout's **unchanged source bytes**
from `knowco2/buttons.py` in a separate module namespace per instance. It reads
hold thresholds from the checkout's `knowco2/config.py`; it does not copy gesture
logic or use a frozen marketing firmware payload. It supplies the actual D0/D1/D2
polarities, 15 ms scanners, bounded queues, wrapped ticks, and USB-only input
power epochs. There are no network calls, added packages, or AWS resources.

Pin changes occur before scans at equal timestamps; scans precede application
polling. Suspending application service does not suspend hardware scanning.
Queued event timestamps, not the eventual polling time, classify holds.
Independent namespaces/clocks/queues allow parallel replay without changing
`sys.modules`, the host clock, or another virtual device.

Power removal abandons runtime objects without invoking firmware shutdown,
`deinit`, a final poll, or a flush. This only qualifies **input-state lifecycle**:
no filesystem, full boot, sensor, USB enumeration, or electrical model exists yet.
Holding a button at boot is explicitly unsupported, not silently treated as a
passing hardware scenario. Declared `main`/`info` context is a fixture input;
`code.py` is not running and application screen transitions are not tested.

The model uses CircuitPython 10.2.1 source semantics for queue overflow: a full
queue rejects the new event, sets overflow, and retains earlier events. This was
checked against `shared-module/keypad/EventQueue.c` (blob
`3130d8b59a1d777c694ca72d349f6872f20dfad2`) and `Keys.c` (blob
`80dcf36e163ad439e06de6cb2ee4c7c908ce6339`) at the upstream 10.2.1 tag.
Some rendered API documentation describes discarding the oldest event instead;
do not silently change this profile without checking the pinned runtime source.
The deterministic scanner is a behavioral model, not a simulation of interrupt
jitter, mechanical bounce, or electrical button contacts.

Upstream references:
- https://github.com/adafruit/circuitpython/blob/10.2.1/shared-module/keypad/EventQueue.c
- https://github.com/adafruit/circuitpython/blob/10.2.1/shared-module/keypad/Keys.c

## Run and replay

From the repository root, using Python 3.12 (standard library only):

```sh
python3 -m unittest discover -s tests/virtual_device -p 'test_*.py' -v
python3 tests/virtual_device/run.py --workers 4 --output test-results/virtual-input/report.json
python3 tests/virtual_device/run.py --case INPUT-004 --output test-results/virtual-input/replay.json
```

Nine explicit data-driven scenarios cover taps, holds, two taps during an
8-second stall, complete and incomplete A+B overlaps, hard power loss, tick wrap,
context guards, and overflow. Model/runner tests verify isolation, repeatability,
strict input rejection, and sensitivity to a deliberately broken gesture constant.
Serial and four-worker results must match exactly.

Reports retain the actual checked-out git revision (including a PR merge commit),
dirty-state indication, executed source/config/model/fixture hashes, pin/scanner
and action timestamps, power epochs, expectations, and observed results.
They explicitly set `release_qualified: false` and list unexecuted subsystems.
A passing input case never marks its parent product-acceptance scenario passed.
Do not compare simulated timing to an unapproved human-response budget.

## Defects reproduced and fixed

1. A+B held for the threshold and released entirely during a blocked application
   produced no unlock gesture. Evaluate the first release's timestamp before
   clearing combo state; preserve one-shot behavior and both release orders.
2. Queue overflow cleared queued releases but left the gesture engine believing
   A+B were still down. A released short combination could later mature into an
   unlock event. On overflow, discard pending/incomplete gesture state and return;
   require fresh presses. Losing ambiguous input is safer than inventing presence.

These are narrow production button changes. Before releasing, repeat both cases
on an engineering board, confirm ordinary gestures and screen behavior, and check
that overflow cannot open an OTA physical-presence window. No device was flashed
or physically qualified by this PR. This is not evidence of a remote exploit.

## CI and repository boundaries

The existing firmware `Firmware CI` job runs the replay and model checks on
`codebuild-knowco2-firmware-${{ github.run_id }}-${{ github.run_attempt }}`.
No hosted fallback or new role is introduced. The new artifact action is pinned,
retention is seven days, and reports contain synthetic input data only. Existing
root `test_sensors.py` is now invoked as legacy wrapper regression coverage; its
fake drivers do not prove actual sensor/driver compatibility. PR #23 contains the
separate SCD43/upstream-I2C work and is not merged or replaced here.

PR jobs reject fork sources; read-only GitHub permissions and that condition are
not AWS isolation. Verify service-role, webhook, connection and network policies
before broadening trust. Nothing here changes deployed IAM or Terraform state.
Any future browser image/project, concurrency, storage or IAM changes require a
reviewed PR in `knowco2-cloud`, not manual infrastructure changes.

Test tooling lives under `tests/`, outside the shipping package. Existing release
builders explicitly stage firmware/package/assets, not this bench. The Mac
flasher and production records remain untouched. This input test module is not a
sandbox for running untrusted firmware on a privileged machine.

## Acceptance catalog and next implementation boundary

These retain the approved catalog IDs. **All full-product scenarios remain
unimplemented in this first PR.** A supporting input test is not full coverage.

| ID | Product journey | Current supporting work / next boundary |
| --- | --- | --- |
| VDA-001 | Cold USB-only boot | Full boot/runtime/display adapter pending |
| VDA-002 | Power removal and committed settings | Input epoch reset only; persistence pending |
| VDA-003 | Physical taps and display modes | Raw pin/gesture replay; model/screen wiring pending |
| VDA-004 | Holds, combinations, stuck buttons | Gesture checks; real screen/actions pending |
| VDA-005 | Interaction during cloud stall | Suspended application service; real transport pending |
| VDA-006 | Queue overflow and tick wrap | Input regression coverage; target qualification pending |
| VDA-007 | Sensor absent at boot | Bus model and actual driver pending |
| VDA-008 | Sensor loss after valid readings | Bus fault and stale-display oracle pending |
| VDA-009 | Sensor recovery/restart | Recovery and reboot requirements pending |
| VDA-010 | Bad protocol/CRC frames | Reuse/extend independently specified upstream-driver tests |
| VDA-011 | Candidate sensor profiles/SCD43 | Integrate approved PR #23 tuple; physical checks required |
| VDA-012 | High, falling and stale readings | Candidate renderer/visible freshness checks pending |
| VDA-013 | Network onboarding/recovery | Real portal with modeled network pending |
| VDA-014 | Independent internet/cloud failures | Firmware transport fixtures pending |
| VDA-015 | Interrupted settings save | Durable-write model pending |
| VDA-016 | Full/read-only/corrupt storage | Filesystem faults pending |
| VDA-017 | USB power/data/service mode | Data enumeration and boot policy pending |
| VDA-018 | Invalid/interrupted update | Extend existing updater tests with power boundaries |
| VDA-019 | Low-power/fault combinations | Actual driver and app scheduler pending |
| VDA-020 | Accelerated endurance | Whole-world scheduler and bounded state pending |
| VDA-021 | Parallel device isolation | Input namespaces verified; whole-runtime isolation pending |
| VDA-022 | Goal-driven virtual user | Deterministic visible journeys first; exploration later |

Next vertical slice: reuse the real CAD/front-panel assets; audit the existing
website Pyodide/shims; load an exact candidate; run actual application dispatch
and display code; map pointer down/up/cancel to pin transitions; capture the
firmware framebuffer. Do not implement a separate JS mode machine or substitute
an action log for the device screen. Qualify display/pin geometry on real hardware.
This slice should boot, visibly change mode, lose its sensor, and power-cycle.

Later, the Mac flasher's separate Engineering Acceptance path should install the
same source/dependency/compiler manifest, verify installed output hashes, and
record physical results without consuming production serials or manufacturing
PASS records. Keep its safe-unmount interlocks; power-cut experiments belong on
an explicitly approved engineering unit. A diagnostic image passing does not
qualify the shipping-equivalent image.
