from pathlib import Path
import os
R = Path(os.environ['KNOWCO2_INTEGRATION_ROOT'])
def edit(name, old, new):
    p=R/name
    s=p.read_text()
    assert old in s, (name, old[:80])
    p.write_text(s.replace(old,new))
ci=(R.parent/'pr24/.github/workflows/ci.yml').read_text()
a=ci.index('      - name: Run hardware-free behavioral suites')
b=ci.index('      - name: Replay virtual input scenarios',a)
ci=ci[:a]+'''      - name: Run every hardware-free suite in an isolated interpreter
        env:
          PYTHONDONTWRITEBYTECODE: "1"
        run: python3 tools/run_tests.py

      - name: Report release blockers (not a production approval)
        run: python3 tools/release_gate.py --report

'''+ci[b:]
ci=ci[:ci.index('\n  ota-staged-install:')]+ '\n'
ci=ci.replace('actions/checkout@v4','actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2')
ci=ci.replace('actions/setup-python@v5','actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0')
ci=ci.replace('    name: Behavioral tests\n','    name: Behavioral tests\n    timeout-minutes: 20\n')
(R/'.github/workflows/ci.yml').write_text(ci.rstrip()+'\n')
edit('tools/sign_ota.py','import hashlib\n','import hashlib\nimport json\n')
edit('tools/sign_ota.py','LIBRARY_SET_ID = "cp10.2.1-bundle20260520-3a88e2948952"\nMPY_ABI = "C6.0.31"', '''def locked_profile():
    from tools.firmware_dependencies import load_lock, validate_lock
    lock = load_lock(ROOT / "dependencies/firmware-build-lock.json")
    validate_lock(lock)
    if lock["board"]["id"] != security.BOARD or lock["circuitpython"]["version"] != security.RUNTIME:
        raise ValueError("OTA verifier and dependency lock board/runtime mismatch")
    header = lock["mpy_cross"]["expected_mpy_header"]
    abi = chr(header[0]) + ".".join(str(value) for value in header[1:])
    return lock, lock["library_bundle"]["library_set_id"], abi


LOCK, LIBRARY_SET_ID, MPY_ABI = locked_profile()''')
edit('tests/test_ota_staged.py','LIBRARY_SET_ID="cp10.2.1-bundle20260520-3a88e2948952",','LIBRARY_SET_ID=signer.LIBRARY_SET_ID,')
edit('tools/build_security_test.py','    if "CircuitPython 10.2.1 " not in version:\n        raise ValueError("The lock requires CircuitPython 10.2.1 mpy-cross")', '''    sys.path.insert(0, str(ROOT))
    from tools.sign_ota import locked_profile
    lock, library_set_id, mpy_abi = locked_profile()
    runtime_version = lock["circuitpython"]["version"]
    if "CircuitPython " + runtime_version + " " not in version:
        raise ValueError("Compiler does not match the locked OTA runtime")''')
edit('tools/build_security_test.py','ROOT / "lib-2026-05-20-release",','ROOT / lock["library_bundle"]["vendored_path"],')
edit('tools/build_security_test.py','"circuitpython": "10.2.1", "compiler": version.strip(),','"circuitpython": runtime_version, "library_set_id": library_set_id,\n            "mpy_abi": mpy_abi, "compiler": version.strip(),')
edit('tools/build_security_test.py','        largest = max(p.stat().st_size for p in image.rglob("*") if p.is_file())\n        required = full_allocation + upload_allocation + largest + 32768 + 32768', '''        staged_allocation = allocation(image)
        required = full_allocation + upload_allocation + staged_allocation + RESERVE + 32768''')
edit('tools/build_security_test.py','        # Leave conservative working space for the resident upload, a largest\n        # staged member, and the old flasher knowco2_app.mpy (budgeted 32 KiB).','        # Budget the resident upload, ALL staged files, reserve, and the old\n        # flasher knowco2_app.mpy. Per-file low-space fallback is disabled.')
edit('knowco2/ota_security.py','    if current is not None and sequence == current["sequence"]:\n        _require(not current["complete"] and _equal(plan["digest"], current["digest"]),\n                 "OTA replay or conflicting same-version package rejected")', '''    if not current["complete"]:
        _require(sequence == current["sequence"] and _equal(plan["digest"], current["digest"]),
                 "OTA replay or conflicting same-version/pending package rejected; retry exact pending manifest")''')
p=R/'knowco2/web/routes.py';s=p.read_text()
a=s.index('    largest = max(e["uncomp_size"] for _, e in safe)',s.index('def _process_zip_update'))
b=s.index('    # Never replace an executable',a)
s=s[:a]+'''    # Full staging is required. Do not silently fall back to committing files
    # while extracting a package: that increases the mixed-version window.
    # This is a mitigation, NOT atomic whole-image/power-fail recovery.
    cluster = 4096
    directories = set()
    stage_bytes = 0
    for name, entry in safe:
        stage_bytes += max(cluster, ((entry["uncomp_size"] + cluster - 1) // cluster) * cluster)
        parts = name.split("/")[:-1]
        for count in range(1, len(parts) + 1):
            directories.add("/".join(parts[:count]))
    required = stage_bytes + (len(directories) + 1) * cluster + 98304
    if free is None or free < required:
        abort("Not enough verified free space for full staging and recovery reserve")
        return
    staged = []
    try:
        for name, entry in safe:
            target = stage_root + "/" + name
            extract(entry, target)
            staged.append((name, target))
    except Exception as error:
        abort("Staging failed: " + str(error))
        return
''' + s[b:]
a=s.index('        if per_file:',s.index('    installed = 0',s.index('def _process_zip_update')))
b=s.index('        ota_security.complete_version(plan)',a)
s=s[:a]+'''        for name, target in staged:
            _wd_feed()
            commit(name, target)
            installed += 1
'''+s[b:]
s=s.replace('    mode = " (low-space per-file mode)" if per_file else ""','    mode = " (full staging)"')
s=s.replace('    total = sum(e["uncomp_size"] for _, e in safe)\n','')
p.write_text(s)
p=R/'tests/test_ota_staged.py';s=p.read_text()
a=s.index('    def test_per_file_mode_and_pending_exact_retry(');b=s.index('    def test_reservation_failure',a)
s=s[:a]+'''    def test_low_space_does_not_fall_back_to_live_per_file_install(self):
        original_nvm = bytes(self.nvm)
        with patch.object(routes, "_fs_free_bytes", return_value=100000):
            ok, message = self.install()
        self.assertFalse(ok)
        self.assertIn("full staging", message)
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(bytes(self.nvm), original_nvm)
        self.assertFalse(self.reset)

    def test_unknown_free_space_fails_before_reservation(self):
        original_nvm = bytes(self.nvm)
        with patch.object(routes, "_fs_free_bytes", return_value=None):
            self.assertFalse(self.install()[0])
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(bytes(self.nvm), original_nvm)

    def test_pending_exact_retry_after_caught_commit_failure(self):
        import os
        real_rename = os.rename
        failed = [False]
        def fail_once(source, target):
            if str(source).endswith("ota_staged/code.py") and not failed[0]:
                failed[0] = True
                raise OSError("injected commit failure; not a power-cut simulation")
            return real_rename(source, target)
        with patch.object(os, "rename", side_effect=fail_once):
            ok, message = self.install()
        self.assertFalse(ok)
        self.assertIn("Retry this exact", message)
        self.assertFalse(ota._state(self.nvm)["complete"])
        self.package(sequence=3)
        self.assertFalse(self.install()[0])
        self.package(sequence=2)
        self.assertTrue(self.install()[0])
        self.assertTrue(ota._state(self.nvm)["complete"])

''' + s[b:];p.write_text(s)
edit('knowco2/version.py','FIRMWARE_VERSION = "RC-54-USB-v1-test1"','FIRMWARE_VERSION = "1.0.0-dev.1"')
(R/'.github/workflows/release.yml').write_text('''name: Release readiness gate (publication disabled)

on:
  push:
    tags: ["v*.*.*"]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  release-readiness:
    runs-on: codebuild-knowco2-firmware-${{ github.run_id }}-${{ github.run_attempt }}
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          persist-credentials: false
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0
        with:
          python-version: "3.12"
      - name: Enforce unresolved 1.0.0 release gates
        run: python3 tools/release_gate.py --enforce
      - name: No production publishing implementation is approved
        run: |
          echo "Production signing, protected approval and exact-artifact qualification are required."
          exit 1
''')
p=R/'tests/test_security_build.py';s=p.read_text();a=s.index('    def test_production_release_workflow_is_byte_identical_to_base');b=s.index('\n\nif __name__',a)
s=s[:a]+'''    def test_production_release_workflow_is_fail_closed(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("contents: read", workflow)
        self.assertIn("python3 tools/release_gate.py --enforce", workflow)
        self.assertIn("exit 1", workflow)
        for forbidden in ("contents: write", "action-gh-release", "gh release", "git push"):
            self.assertNotIn(forbidden, workflow)

    def test_nested_virtual_tests_are_not_dropped_by_integration(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("python3 tools/run_tests.py", ci)
        self.assertIn("unittest discover -s tests/virtual_device", ci)
        self.assertIn("tests/virtual_device/run.py --workers 4", ci)
        self.assertIn("persist-credentials: false", ci)
        self.assertIn("head.repo.full_name == github.repository", ci)

    def test_signer_profile_matches_effective_dependency_lock(self):
        lock, library_set, abi = sign_ota.locked_profile()
        self.assertEqual(library_set, lock["library_bundle"]["library_set_id"])
        self.assertEqual(sign_ota.LIBRARY_SET_ID, library_set)
        self.assertEqual(sign_ota.MPY_ABI, abi)
''' + s[b:];p.write_text(s)
p=R/'.github/workflows/security-test.yml';s=p.read_text()
s=s.replace("    if: github.event_name == 'push' || github.head_ref == 'fix/security-test-ota'", "    if: (github.event_name == 'push' && github.ref == 'refs/heads/fix/security-test-ota') || (github.event_name == 'pull_request' && github.head_ref == 'fix/security-test-ota' && github.event.pull_request.head.repo.full_name == github.repository)")
s=s.replace('actions/checkout@v4','actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2').replace('actions/setup-python@v5','actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0').replace('actions/upload-artifact@v4','actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2')
p.write_text(s)
edit('README.md', '# KnowCO2 Firmware — CircuitPython\n', '# KnowCO2 Firmware — CircuitPython\n\n> **1.0.0 integration / development only.** See [release blockers and test boundaries](docs/releases/1.0.0-readiness.md). No customer release or device update is approved; legacy unsigned publication is disabled on this branch.\n')
edit('SECURITY_TESTING.md', '# Security test branch — TEST ONLY, not a production release\n', '# Security test branch — TEST ONLY, not a production release\n\n> **Integration override:** this is the historical #22 runbook, not an approved device procedure for the combined tree. The old bundle is incompatible with the changed dependency set. Full staging is now mandatory; low-space per-file fallback has been removed. Read [1.0.0 readiness](docs/releases/1.0.0-readiness.md) and keep this build off customer devices.\n')
p=R/'tests/README.md'
p.write_text(p.read_text()+'''\n\n## 1.0.0 integration checks

`python3 tools/run_tests.py` (from the repository root) runs the isolated top-level suites, including signed installer, configured-state preservation and release-gate tests. CI additionally runs `python3 -m unittest discover -s tests/virtual_device -p "test_*.py" -v` and the nine input replays. A passing report does not clear the separate physical OTA/recovery gates in `docs/releases/1.0.0-readiness.md`.
''')
