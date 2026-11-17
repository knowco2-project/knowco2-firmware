#!/usr/bin/env python3
"""Build conspicuously TEST-ONLY local-flasher and signed OTA artifacts.

Never publishes a release, touches a connected device, or uses production keys.
The public test fixture is intentionally unfit for production trust.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPACITY = 983040
RESERVE = 98304
CLUSTER = 4096


def run(*args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def copy_source(destination):
    destination.mkdir(parents=True)
    for name in ("code.py", "boot.py"):
        shutil.copy2(ROOT / name, destination / name)
    for name in ("knowco2", "assets"):
        shutil.copytree(ROOT / name, destination / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.mpy"))
    legal = destination / "assets/legal"
    legal.mkdir(exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, legal / name)


def set_version(source, label):
    path = source / "knowco2/version.py"
    text = path.read_text()
    text, count = re.subn(r'^FIRMWARE_VERSION\s*=.*$',
                         'FIRMWARE_VERSION = ' + repr(label), text, flags=re.M)
    if count != 1:
        raise ValueError("Expected exactly one FIRMWARE_VERSION")
    path.write_text(text)


def zip_tree(source, output):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zi = zipfile.ZipInfo(path.relative_to(source).as_posix(), (2026, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o100644 << 16
                zf.writestr(zi, path.read_bytes())


def allocation(source):
    # Conservative 4 KiB FAT allocation including one cluster per directory.
    return sum(max(CLUSTER, ((p.stat().st_size + CLUSTER - 1) // CLUSTER) * CLUSTER)
               if p.is_file() else CLUSTER for p in source.rglob("*"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mpy-cross", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise ValueError("--commit must be the exact 40-character source commit SHA")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                    cwd=ROOT, text=True).strip()
    if head != args.commit or dirty:
        raise ValueError("Scoped OTA requires the exact clean committed source tree; --commit must equal HEAD")
    compiler = args.mpy_cross.resolve()
    version = subprocess.check_output([str(compiler), "--version"], text=True)
    if "CircuitPython 10.2.1 " not in version:
        raise ValueError("The lock requires CircuitPython 10.2.1 mpy-cross")
    run(sys.executable, "tools/firmware_dependencies.py", "verify")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    short = args.commit[:12]
    name = "knowco2-security-test-" + short
    with tempfile.TemporaryDirectory(prefix="knowco2-test-build-") as temp:
        root = Path(temp)
        source = root / "source"
        copy_source(source)
        run(sys.executable, "tools/sign_ota.py", "--write-test-trust",
            source / "knowco2/ota_trust.py", "--baseline", "1", "--build-id", args.commit, "--test-key")
        trust_values = {}
        for node in ast.parse((source / "knowco2/ota_trust.py").read_text()).body:
            if isinstance(node, ast.Assign):
                trust_values[node.targets[0].id] = ast.literal_eval(node.value)
        bootstrap_trust = types.SimpleNamespace(**trust_values)
        set_version(source, "SECURITY-TEST-" + short + "-BOOTSTRAP-1")
        marker = source / "assets/SECURITY_TEST_ONLY.txt"
        marker.write_text("TEST ONLY. Publicly known signing fixture. Never use on customer/production devices.\n"
                          "Source commit: " + args.commit + "\n"
                          "Physical local-flasher bootstrap required. See SECURITY_TESTING.md.\n")
        zip_tree(source, out / (name + "-source.zip"))

        image = root / "image"
        run(sys.executable, "tools/sign_ota.py", "--write-test-trust",
            source / "knowco2/ota_trust.py", "--baseline", "2", "--build-id", args.commit, "--test-key")
        # No dependency update: the same pinned bundled .mpy files are used.
        set_version(source, "SECURITY-TEST-" + short + "-OTA-2")
        run(sys.executable, "tools/build_device_image.py", "--source-root", source,
            "--library-root", ROOT / "lib-2026-05-20-release",
            "--mpy-cross", compiler, "--output-root", image, "--allow-test-trust")
        full_allocation = allocation(image)
        if full_allocation + RESERVE > CAPACITY:
            raise ValueError("Compiled image exceeds conservative flasher filesystem budget")
        run(sys.executable, "tools/validate_release_tree.py", image)
        # The local bootstrap installs ALL hardened modules. This deliberately
        # scoped OTA exercises signed upgrade on the same exact base build; a
        # full archive plus staging cannot fit the standard device volume.
        scope = {"code.py", "boot.py", "knowco2/version.mpy", "knowco2/ota_trust.mpy"}
        for path in image.rglob("*"):
            if path.is_file() and path.relative_to(image).as_posix() not in scope:
                path.unlink()
        ota = out / (name + "-ota-sequence-2.zip")
        run(sys.executable, "tools/sign_ota.py", "--source-root", image,
            "--output", ota, "--sequence", "2", "--build-id", args.commit, "--test-key")
        # Leave conservative working space for the resident upload, a largest
        # staged member, and the old flasher knowco2_app.mpy (budgeted 32 KiB).
        upload_allocation = ((ota.stat().st_size + CLUSTER - 1) // CLUSTER) * CLUSTER
        largest = max(p.stat().st_size for p in image.rglob("*") if p.is_file())
        required = full_allocation + upload_allocation + largest + 32768 + 32768
        if required > CAPACITY:
            raise ValueError("Scoped OTA plus staging exceeds conservative device flash budget")
        # A separately signed lower version proves antirollback (never install first).
        rollback = out / (name + "-negative-rollback-sequence-1.zip")
        run(sys.executable, "tools/sign_ota.py", "--source-root", image,
            "--output", rollback, "--sequence", "1", "--build-id", args.commit, "--test-key")
        # Tampering without resigning must be rejected before live-file writes.
        tampered = out / (name + "-negative-tampered.zip")
        raw = bytearray(ota.read_bytes())
        with zipfile.ZipFile(ota) as archive:
            member = archive.getinfo("code.py")
            offset = member.header_offset
            n = int.from_bytes(raw[offset + 26:offset + 28], "little")
            e = int.from_bytes(raw[offset + 28:offset + 30], "little")
            raw[offset + 30 + n + e] ^= 1
        tampered.write_bytes(raw)
        # Independently exercise the device verifier on the exact output bytes.
        sys.path.insert(0, str(ROOT))
        from knowco2 import ota_security
        nvm = bytearray(256)
        ota_security.initialize_baseline(nvm=nvm, trust=bootstrap_trust)
        plan = ota_security.verify_package(str(ota), nvm=nvm, trust=bootstrap_trust)
        if {e["name"] for e in plan["entries"]} != scope:
            raise ValueError("Unexpected scoped OTA inventory")
        for rejected in (rollback, tampered):
            try:
                ota_security.verify_package(str(rejected), nvm=nvm, trust=bootstrap_trust)
            except ota_security.OTARejected:
                pass
            else:
                raise ValueError("Negative artifact was accepted: " + rejected.name)
        ota_security.reserve_version(plan, nvm=nvm, trust=bootstrap_trust)
        ota_security.complete_version(plan, nvm=nvm)
        try:
            ota_security.verify_package(str(ota), nvm=nvm, trust=bootstrap_trust)
        except ota_security.OTARejected:
            pass
        else:
            raise ValueError("Completed artifact replay was accepted")
        report = {
            "schema": "knowco2.security-test-build.v1", "production_ready": False,
            "test_only_publicly_known_key": True, "source_commit": args.commit,
            "circuitpython": "10.2.1", "compiler": version.strip(),
            "bootstrap_sequence": 1, "ota_sequence": 2,
            "filesystem_capacity_bytes": CAPACITY, "filesystem_reserved_bytes": RESERVE,
            "estimated_compiled_full_allocation_bytes": full_allocation,
            "ota_archive_bytes": ota.stat().st_size,
            "ota_scope": sorted(scope), "required_base_commit": args.commit,
            "estimated_peak_flash_bytes": required,
            "full_release_ota_supported_by_this_artifact": False,
            "host_artifact_verification": "PASS: real verifier accepts scoped OTA; rejects tamper, rollback, completed replay",
            "hardware_tests": "NOT RUN",
            "hardware_gates": ["flasher filesystem size", "RSA time/heap/watchdog", "TLS/CA matrix",
                               "OTA free-space", "power loss/recovery", "sensor/display/network soak"],
        }
        report["files"] = {p.name: {"bytes": p.stat().st_size,
                                      "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in sorted(out.glob(name + "-*.zip"))}
        (out / (name + "-build.json")).write_text(json.dumps(report, indent=2) + "\n")
        shutil.copy2(ROOT / "SECURITY_TESTING.md", out / "SECURITY_TESTING.md")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
