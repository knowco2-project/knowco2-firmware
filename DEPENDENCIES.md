# Firmware dependency policy

The `knowco2-firmware` repository is the source of truth for the software tuple
used to build and manufacture a KnowCO2 device:

- target board and checked-in CircuitPython UF2;
- Adafruit CircuitPython bundle release and asset hashes;
- exact vendored `lib` snapshot;
- matching CircuitPython `mpy-cross` source commit and `.mpy` ABI;
- release compatibility metadata consumed by the KnowCO2 Flasher.

The approved tuple is recorded in
[`dependencies/firmware-build-lock.json`](dependencies/firmware-build-lock.json).
Release workflows and the flasher must consume that lock or a release manifest
generated from it. They must not independently choose the newest library folder,
newest bundle, or newest CircuitPython version.

## Normal verification

Run this before changing firmware dependencies or creating a release:

```bash
python3 tools/firmware_dependencies.py verify
python3 tests/test_firmware_dependencies.py
```

The verifier checks the checked-in UF2 Git blob, exact vendored library Git tree,
top-level component inventory, CircuitPython `.mpy` header, notices, major-version
compatibility, and compiler/runtime version match.

## Checking for updates

The scheduled dependency monitor compares the lock with Adafruit's latest
published bundle and opens or refreshes one review issue when a newer bundle is
available. It does **not** update the lock, modify the vendored directory, or
change what the production flasher installs.

A bundle or CircuitPython update is a candidate until all of the following are
complete:

1. Update the exact URLs, SHA-256 digests, version/commit pins, vendored path,
   vendored Git tree, and `library_set_id` in the lock.
2. Replace the vendored library snapshot with only the reviewed KnowCO2
   dependencies.
3. Update `THIRD_PARTY_NOTICES.md` when the component inventory or notices
   change.
4. Pass CI and build the full, development, and OTA artifacts.
5. Test startup, SCD41 readings, display, buttons, Wi-Fi AP/STA, browser
   onboarding, cloud/MQTT, OTA, and rollback on a marked engineering unit.
6. Run an extended soak test before marking the tuple production-qualified.

Do not replace the bundle simply because a newer date exists. Update when a
relevant fix or feature justifies the qualification work, or as part of a
planned dependency maintenance cycle.

## Release metadata

The companion release-integration change generates two machine-readable files:

- `knowco2-compatibility-vX.Y.Z.json` describes supported CircuitPython versions
  using the contract introduced by the flasher's selectable-profile work.
- `knowco2-build-vX.Y.Z.json` records the exact board, UF2, bundle,
  `library_set_id`, vendored tree, and compiler source commits used for the
  release.

This keeps manufacturing traceability in the firmware release while allowing
the flasher to focus on USB installation, hardware qualification, serial-number
allocation, and production records.
