# Security test branch — TEST ONLY, not a production release

These artifacts deliberately use a **publicly known test signing key**. Anyone
can sign packages for this test trust profile. They exercise enforcement and
recovery; they do **not** establish production firmware authenticity. The normal
source tree ships no trusted key and rejects network OTA. Production key custody,
signing/release integration, and device qualification remain separate gates.

## What to download

Use the artifacts from the **Security test artifacts (no release)** workflow for
the exact branch commit. It uploads a bundle containing:

- `knowco2-security-test-<commit>-source.zip`: readable application source for
  the existing local firmware flasher (bootstrap sequence 1).
- `knowco2-security-test-<commit>-ota-sequence-2.zip`: signed application-only
  **same-build scoped test OTA**: `code.py`, `boot.py`, compiled version and
  trust modules only. All hardened application modules were installed by the
  physical source bootstrap. The signed manifest requires that exact bootstrap
  commit and locked dependency/ABI identity; it is not a full-release updater.
- `*-negative-rollback-sequence-1.zip`: valid test signature, deliberately old
  sequence; must be rejected after bootstrap/upgrade.
- `*-negative-tampered.zip`: altered executable byte, unchanged signature;
  must be rejected without changing live firmware.
- `*-build.json`: exact source SHA, artifact SHA-256, size estimates and
  unperformed hardware gates. All UI version labels say `SECURITY-TEST`.

No workflow in this branch creates a release, tag, deployment or production key.
The existing release workflow is intentionally unchanged and must **not** be run
for these artifacts. The branch artifact workflow has read-only repository
permissions. The dependency monitor fix is proposed only; scheduling continues
from the default branch until reviewed/merged.

## Bootstrap on a spare device

**Do not use a customer or production unit.** The existing flasher's source
installation is a full manufacturing erase, not a nondestructive OTA upload.
Back up any wanted settings, identity and calibration through your established
process first. A flash can erase credentials and settings. This task has not
flashed any hardware.

1. Check the bundle's exact commit and SHA-256 against its build report.
2. Extract `*-source.zip`. In the existing firmware flasher, choose the local
   source **folder**. This avoids its release-tag ZIP-filename matching logic.
   Use the qualified Feather ESP32-S3 Reverse TFT / CircuitPython **10.2.1**
   profile. Do not select an online production release as a substitute.
3. Let the flasher compile the readable source with its pinned compiler and
   libraries. Its normal `code.py` shim / `knowco2_app.mpy` output is expected.
   Record the actual `SECURITY-TEST-…-BOOTSTRAP-1` version and source commit;
   the catalog-selected release tag alone does not identify this local build.
4. Hold physical **B** while resetting once to initialize the provisioned
   antirollback floor in NVM. The initializer only accepts blank journal slots;
   it never resets an existing valid or malformed journal. Then reset normally.
   A normal boot or OTA request cannot initialize missing version state.
5. Confirm sensor/display behavior, setup AP, settings, and the conspicuous test
   version. Keep the device on an isolated test network. Do not provision real
   production cloud credentials.

The old unsigned firmware cannot verify a new key introduced by an unsigned
OTA. Physical local installation is the trust bootstrap. Copying a test key
over the old network updater is **not** a secure bootstrap.

## Exercise the updater

Use `/update` on the physical setup AP, or in STA mode configure an admin
password and hold **A + B** for the physical unlock window. STA OTA requires
**both** controls. Upload credentials use a request header, never a URL query.
Only signed ZIP upload remains; raw `.py` and URL-fetch update paths are removed.

1. Try an old unsigned OTA, a raw Python file, the tampered ZIP and the signed
   sequence-1 negative fixture. Each must fail before replacing live files.
2. Upload the signed sequence-2 ZIP. Watch for success and reboot, then confirm
   `SECURITY-TEST-…-OTA-2`. The OTA intentionally installs a complete `code.py`
   entry point instead of the flasher's bootstrap shim; the old
   `knowco2_app.mpy` is inert. The image retains the same locked libraries and
   all other application modules from the exact signed base build. A package
   from a different branch commit must be rejected rather than mixed in.
3. Retry the completed sequence-2 ZIP: replay must be rejected. Retry the
   sequence-1 negative fixture: downgrade must be rejected. A pending,
   interrupted install can retry **only the exact same signed manifest**.
4. Reboot and repeat downgrade/replay checks to prove NVM persistence. Missing,
   malformed or unavailable version state must fail closed.
5. With a controlled browser test, reject hostile/missing Host and hostile
   Origin/Referer, wrong methods, query-string passwords and STA requests
   missing either physical unlock or the admin password. Verify setup flows
   still work from the intended AP and native clients use the correct Host.

The journal occupies NVM bytes **16–175**; boot recovery owns bytes 0 and 1.
On this ESP32 runtime, each NVM write rewrites the entire CircuitPython NVS blob,
including writes by ordinary boot recovery. The two journal slots are logical
redundancy, **not independent power-fail-safe sectors**. Loss of the whole blob
fails closed for normal OTA and requires deliberate physical recovery.

This is application-level antirollback, not hardware secure boot, a hardware
monotonic counter, or protection against a person with service-volume/REPL/full
flash access. Physical erase/reprovisioning can replace trust and version state.

## Required hardware qualification (NOT RUN by this change)

Record board revision, runtime, commit, artifact hashes and results:

- [ ] Flasher full-image allocation fits its 983040-byte volume with 98304-byte
  reserve and 4096-byte FAT cluster overhead. Capture the flasher's real report.
- [ ] RSA verification time, free heap and watchdog behavior on CP 10.2.1.
- [ ] Upload free-space and staged/per-file installation behavior on the actual
  volume, including existing settings and the obsolete bootstrap app file.
- [ ] Power loss during upload, verification, journal reservation, each commit
  phase and completion, plus whole-NVM-blob loss during ordinary boot/recovery
  writes; recovery through the physical service path.
- [ ] No credential leakage in logs, URLs, status, USB API or browser history.
- [ ] Cloud/MQTT HTTPS verification rejects untrusted/expired/wrong-host certs;
  inspect exact trust roots. The pinned runtime's upstream CA removals remain
  unresolved; an application ZIP does not replace CircuitPython's CA bundle.
- [ ] Sensor/display/buttons, AP/STA onboarding, USB API, reconnect and soak.

Per-file rename avoids truncated individual files but is **not** an atomic
whole-image swap. Power loss can leave mixed versions, and physical recovery
may be required. Do not claim rollback/recovery is hardware-qualified.

## Rebuild without publishing

Build the lock's exact CircuitPython 10.2.1 `mpy-cross` commit, then:

```sh
python3 tools/build_security_test.py --mpy-cross /path/to/mpy-cross \
  --commit "$(git rev-parse HEAD)" --output-dir dist/security-test
```

The builder refuses dirty/uncommitted sources or a --commit different from HEAD.
It verifies the unchanged pinned UF2/library hashes, writes fixture
trust only into temporary artifact staging, compiles application modules, checks
the conservative full-image and scoped-OTA working-space allocation and produces
negative fixtures. A complete signed all-application archive does not fit the
standard volume alongside the installed firmware and staging, so this bundle
intentionally tests a same-base scoped update. Full-release OTA packaging is
still a separate gate; do not drop the exact-base check to reuse this artifact
against another build. No
production credentials are needed or created. Ordinary `build_device_image.py`
rejects a staged `TEST_ONLY` trust profile unless `--allow-test-trust` is explicit.

Before any release: obtain independent cryptographic review, provision a private
production signing key through an approved custody process, integrate signing
into a separately authorized release workflow, resolve the CA qualification
gate and pass all hardware tests above. Do not promote these fixture artifacts.
