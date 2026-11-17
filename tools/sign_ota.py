#!/usr/bin/env python3
"""Produce TEST-ONLY signed OTA ZIPs and isolated test trust configuration.

The checked-in private key is PUBLIC TEST DATA, not a credential. Anyone can
sign with it. These packages must never be promoted to production. Production
key custody, provisioning, signing and rotation require a separate reviewed
implementation; this tool deliberately has no production-signing option.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from knowco2 import ota_security as security

TEST_KEY = ROOT / "tests" / "fixtures" / "ota_test_private.pem"
TEST_KEY_ID = "PUBLIC-INSECURE-TEST-KEY-2026-10"
TEST_BUILD_ID = "0" * 40
LIBRARY_SET_ID = "cp10.2.1-bundle20260520-3a88e2948952"
MPY_ABI = "C6.0.31"


def _build_id(value):
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("build ID must be an exact 40-character commit SHA")
    return value


def test_modulus():
    text = subprocess.check_output(
        ["openssl", "rsa", "-in", str(TEST_KEY), "-noout", "-modulus"],
        stderr=subprocess.DEVNULL, text=True,
    ).strip()
    if not text.startswith("Modulus="):
        raise RuntimeError("Could not read TEST-ONLY RSA modulus")
    return text.split("=", 1)[1].lower()


def write_test_trust(path: Path, baseline: int, build_id=TEST_BUILD_ID):
    _build_id(build_id)
    if not 1 <= baseline <= security.MAX_SEQUENCE:
        raise ValueError("baseline outside bounded release-sequence range")
    if path.resolve() == (ROOT / "knowco2" / "ota_trust.py").resolve():
        raise ValueError("Refusing to replace default source-checkout trust")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '# PUBLIC TEST KEY: ANYONE CAN SIGN. ISOLATED TEST DEVICES ONLY.\n'
        '# Never release this trust configuration to customers.\n'
        f'PUBLIC_KEY_N_HEX = "{test_modulus()}"\n'
        f'KEY_ID = "{TEST_KEY_ID}"\n'
        'TEST_ONLY = True\n'
        f'MIN_SEQUENCE = {baseline}\n'
        f'BUILD_ID = "{build_id}"\n'
        f'LIBRARY_SET_ID = "{LIBRARY_SET_ID}"\n'
        f'MPY_ABI = "{MPY_ABI}"\n', encoding="utf-8",
    )


def _signature(manifest: bytes):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "manifest"
        path.write_bytes(manifest)
        return subprocess.check_output([
            "openssl", "dgst", "-sha256", "-sign", str(TEST_KEY), str(path),
        ])


def manifest_for(files: dict[str, bytes], sequence: int, build_id=TEST_BUILD_ID):
    _build_id(build_id)
    if not 1 <= sequence <= security.MAX_SEQUENCE:
        raise ValueError("sequence outside bounded release-sequence range")
    if not 1 <= len(files) <= security.MAX_ENTRIES:
        raise ValueError("too many files")
    total = 0
    rows = ["KNOWCO2-OTA-1", "board=" + security.BOARD,
            "runtime=" + security.RUNTIME, "key=" + TEST_KEY_ID,
            "channel=TEST-ONLY", "sequence=" + str(sequence),
            "base_build=" + build_id, "library_set=" + LIBRARY_SET_ID,
            "mpy_abi=" + MPY_ABI]
    aliases = set()
    for name, data in sorted(files.items()):
        if not security.valid_path(name) or len(data) > security.MAX_FILE:
            raise ValueError("Unsafe/oversized payload path: " + name)
        alias = name.lower()
        if alias.endswith((".py", ".mpy")):
            alias = alias.rsplit(".", 1)[0]
        if alias in aliases:
            raise ValueError("Ambiguous case or source/bytecode alias: " + name)
        aliases.add(alias)
        total += len(data)
        rows.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {name}")
    if total > security.MAX_TOTAL or not {"code.py", "boot.py"} <= files.keys():
        raise ValueError("Payload is too large or lacks required entrypoints")
    manifest = ("\n".join(rows) + "\n").encode("ascii")
    if len(manifest) > security.MAX_MANIFEST:
        raise ValueError("Manifest exceeds device parser bound")
    return manifest


def write_package(files: dict[str, bytes], output: Path, sequence: int, build_id=TEST_BUILD_ID):
    manifest = manifest_for(files, sequence, build_id)
    signature = _signature(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED,
                         allowZip64=False) as archive:
        for name, data in [(security.MANIFEST_NAME, manifest),
                           (security.SIGNATURE_NAME, signature)] + sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 26, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (0o100644 << 16)
            archive.writestr(info, data)
    if output.stat().st_size > security.MAX_PACKAGE_BYTES:
        output.unlink()
        raise ValueError("Signed OTA exceeds 512 KiB upload bound")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-key", action="store_true", required=True,
                        help="Explicitly acknowledge PUBLIC INSECURE TEST key")
    parser.add_argument("--build-id", required=True, help="Exact base/source commit SHA")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sequence", type=int)
    parser.add_argument("--write-test-trust", type=Path)
    parser.add_argument("--baseline", type=int)
    args = parser.parse_args()
    if args.write_test_trust:
        if args.baseline is None:
            parser.error("--write-test-trust requires --baseline")
        write_test_trust(args.write_test_trust, args.baseline, args.build_id)
    if args.output:
        if args.source_root is None or args.sequence is None:
            parser.error("--output requires --source-root and --sequence")
        files = {}
        for path in sorted(args.source_root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Refusing symlink in OTA image")
            if path.is_file():
                name = path.relative_to(args.source_root).as_posix()
                if security.valid_path(name):
                    files[name] = path.read_bytes()
                else:
                    raise ValueError("Unexpected file in OTA image: " + name)
        write_package(files, args.output, args.sequence, args.build_id)
        print(f"TEST ONLY (public signing key): {args.output}")
    elif not args.write_test_trust:
        parser.error("request --output or --write-test-trust")


if __name__ == "__main__":
    main()
