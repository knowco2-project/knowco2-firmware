#!/usr/bin/env python3
"""Host security boundary tests; no hardware or root filesystem writes."""

import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from knowco2 import ota_security as ota

spec = importlib.util.spec_from_file_location("sign_ota", ROOT / "tools/sign_ota.py")
signer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signer)


class SignedOTATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modulus = signer.test_modulus()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "ota.zip"
        self.trust = types.SimpleNamespace(
            PUBLIC_KEY_N_HEX=self.modulus, KEY_ID=signer.TEST_KEY_ID,
            TEST_ONLY=True, MIN_SEQUENCE=1, BUILD_ID=signer.TEST_BUILD_ID,
            LIBRARY_SET_ID=signer.LIBRARY_SET_ID, MPY_ABI=signer.MPY_ABI,
        )
        self.nvm = bytearray(256)
        self.nvm[0:2] = b"\xc2\x03"
        ota.initialize_baseline(nvm=self.nvm, trust=self.trust)
        self.files = {"code.py": b"# test application\n", "boot.py": b"# test boot\n",
                      "knowco2/example.py": b"VALUE = 2\n"}
        signer.write_package(self.files, self.path, 2)

    def verify(self):
        return ota.verify_package(str(self.path), nvm=self.nvm, trust=self.trust)

    def replace_member(self, name, data, resign=False):
        with zipfile.ZipFile(self.path) as archive:
            members = [(i, archive.read(i)) for i in archive.infolist()]
        members = [(i, data if i.filename == name else value) for i, value in members]
        if resign:
            manifest = next(v for i, v in members if i.filename == ota.MANIFEST_NAME)
            members = [(i, signer._signature(manifest) if i.filename == ota.SIGNATURE_NAME else v)
                       for i, v in members]
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_STORED) as archive:
            for i, v in members:
                archive.writestr(i, v, compresslevel=9)

    def test_valid_signature_and_staged_readback(self):
        calls = []
        plan = ota.verify_package(str(self.path), watchdog=lambda: calls.append(1),
                                  nvm=self.nvm, trust=self.trust)
        self.assertEqual(plan["sequence"], 2)
        self.assertTrue(plan["test_only"])
        self.assertEqual({e["name"] for e in plan["entries"]}, set(self.files))
        self.assertTrue(calls)
        entry = plan["entries"][0]
        stage = self.root / "stage"
        stage.write_bytes(self.files[entry["name"]])
        ota.verify_staged_file(entry, str(stage))
        stage.write_bytes(b"changed")
        with self.assertRaises(ota.OTARejected):
            ota.verify_staged_file(entry, str(stage))

    def test_default_checkout_has_no_trusted_key(self):
        with self.assertRaisesRegex(ota.OTARejected, "not provisioned"):
            ota.verify_package(str(self.path), nvm=self.nvm)

    def test_unsigned_legacy_zip_rejected(self):
        with zipfile.ZipFile(self.path, "w") as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ota.OTARejected, "Signed manifest"):
            self.verify()

    def test_changed_manifest_without_signature_rejected(self):
        manifest = signer.manifest_for(self.files, 3)
        self.replace_member(ota.MANIFEST_NAME, manifest)
        with self.assertRaisesRegex(ota.OTARejected, "signature verification"):
            self.verify()

    def test_changed_payload_with_recomputed_zip_crc_rejected(self):
        self.replace_member("knowco2/example.py", b"VALUE = 3\n")
        with self.assertRaisesRegex(ota.OTARejected, "SHA-256/size mismatch"):
            self.verify()

    def test_exact_rsa_padding_and_signature_bounds(self):
        for signature in (b"\0" * 256, b"\xff" * 256, b"\0" * 255, b"\0" * 257):
            with self.subTest(length=len(signature), first=signature[0]):
                signer.write_package(self.files, self.path, 2)
                self.replace_member(ota.SIGNATURE_NAME, signature)
                with self.assertRaises(ota.OTARejected):
                    self.verify()

    def test_wrong_board_runtime_key_channel_rejected_even_if_signed(self):
        manifest = signer.manifest_for(self.files, 2)
        substitutions = [(ota.BOARD.encode(), b"wrong_board"), (b"runtime=10.2.1", b"runtime=9.2.8"),
                         (signer.TEST_KEY_ID.encode(), b"wrong_key"),
                         (b"channel=TEST-ONLY", b"channel=production")]
        for old, new in substitutions:
            with self.subTest(old=old):
                signer.write_package(self.files, self.path, 2)
                self.replace_member(ota.MANIFEST_NAME, manifest.replace(old, new), resign=True)
                with self.assertRaises(ota.OTARejected):
                    self.verify()

    def test_bounded_release_sequence(self):
        manifest = signer.manifest_for(self.files, 2)
        for value in (b"0", b"-1", b"02", b"2147483648", b"9" * 100):
            with self.subTest(value=value):
                signer.write_package(self.files, self.path, 2)
                self.replace_member(ota.MANIFEST_NAME,
                                    manifest.replace(b"sequence=2", b"sequence=" + value), resign=True)
                with self.assertRaises(ota.OTARejected):
                    self.verify()

    def test_no_writes_before_validation(self):
        before = bytes(self.nvm)
        self.replace_member("code.py", b"# attacker code!!\n")
        with self.assertRaises(ota.OTARejected):
            self.verify()
        self.assertEqual(bytes(self.nvm), before)

    def test_reserve_retry_complete_replay_and_downgrade(self):
        plan = self.verify()
        ota.reserve_version(plan, nvm=self.nvm, trust=self.trust)
        self.assertEqual(self.verify()["digest"], plan["digest"])
        ota.reserve_version(plan, nvm=self.nvm, trust=self.trust)
        self.assertEqual(self.nvm[0:2], b"\xc2\x03")
        ota.complete_version(plan, nvm=self.nvm)
        with self.assertRaisesRegex(ota.OTARejected, "replay"):
            self.verify()
        signer.write_package(self.files, self.path, 1)
        with self.assertRaisesRegex(ota.OTARejected, "rollback"):
            self.verify()
        signer.write_package(self.files, self.path, 3)
        self.assertEqual(self.verify()["sequence"], 3)

    def test_same_version_other_payload_not_recovery(self):
        ota.reserve_version(self.verify(), nvm=self.nvm, trust=self.trust)
        self.files["code.py"] = b"# different signed build\n"
        signer.write_package(self.files, self.path, 2)
        with self.assertRaisesRegex(ota.OTARejected, "same-version"):
            self.verify()

    def test_blank_state_fails_closed_until_physical_initializer(self):
        self.nvm = bytearray(256)
        with self.assertRaisesRegex(ota.OTARejected, "blank"):
            self.verify()
        self.assertTrue(ota.initialize_baseline(nvm=self.nvm, trust=self.trust))
        self.assertFalse(ota.initialize_baseline(nvm=self.nvm, trust=self.trust))
        self.assertEqual(self.verify()["sequence"], 2)

    def test_corrupt_state_and_missing_nvm_fail_closed(self):
        self.nvm[16:176] = b"!" * 160
        with self.assertRaisesRegex(ota.OTARejected, "corrupt"):
            self.verify()
        with self.assertRaises(ota.OTARejected):
            ota.initialize_baseline(nvm=self.nvm, trust=self.trust)
        with self.assertRaisesRegex(ota.OTARejected, "NVM unavailable"):
            ota.verify_package(str(self.path), nvm=bytearray(2), trust=self.trust)

    def test_torn_reservation_retains_higher_pending_record(self):
        plan = self.verify()
        old = bytes(self.nvm)
        ota.reserve_version(plan, nvm=self.nvm, trust=self.trust)
        self.nvm[96:176] = old[96:176]
        self.assertEqual(self.verify()["digest"], plan["digest"])
        self.nvm[96:176] = b"?" * 80
        self.assertEqual(self.verify()["digest"], plan["digest"])
        self.files["boot.py"] = b"# different\n"
        signer.write_package(self.files, self.path, 2)
        with self.assertRaises(ota.OTARejected):
            self.verify()

    def test_nvm_write_failure_raises_before_install(self):
        class ReadOnly(bytearray):
            def __setitem__(self, key, value):
                raise OSError("simulated flash failure")
        with self.assertRaises(OSError):
            ota.reserve_version(self.verify(), nvm=ReadOnly(self.nvm), trust=self.trust)

    def test_complete_requires_matching_reservation(self):
        with self.assertRaisesRegex(ota.OTARejected, "reservation missing"):
            ota.complete_version(self.verify(), nvm=self.nvm)

    def test_traversal_duplicate_case_alias_and_symlink_rejected(self):
        for name, attrs in [("../escape.py", 0), ("knowco2/../escape.py", 0),
                            ("knowco2/example.py.", 0), ("knowco2./example.py", 0),
                            ("assets/trailing./file.txt", 0),
                            ("code.py", 0), ("CODE.py", 0),
                            ("knowco2/example.mpy", 0), ("assets/link", 0o120777 << 16)]:
            with self.subTest(name=name):
                signer.write_package(self.files, self.path, 2)
                with zipfile.ZipFile(self.path, "a") as archive:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = attrs
                    archive.writestr(info, b"x")
                with self.assertRaises(ota.OTARejected):
                    self.verify()

    def test_compression_encryption_descriptor_zip64_and_trailing_data_rejected(self):
        baseline = self.path.read_bytes()
        mutations = []
        # Raw mutations also preserve signed payload, exercising container-only checks.
        for offset, value in [(6, 1), (6, 8), (8, 8)]:
            changed = bytearray(baseline)
            struct.pack_into("<H", changed, offset, value)
            mutations.append(changed)
        mutations.extend([baseline + b"junk", baseline[:-1], baseline[:20]])
        for data in mutations:
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                with self.assertRaises(ota.OTARejected):
                    self.verify()

    def test_local_central_path_and_size_mismatch_rejected(self):
        baseline = self.path.read_bytes()
        for offset in (30, 18, 22):
            changed = bytearray(baseline)
            changed[offset] ^= 1
            self.path.write_bytes(changed)
            with self.assertRaises(ota.OTARejected):
                self.verify()

    def test_bounded_archive_before_parser_allocation(self):
        self.path.write_bytes(b"x" * (ota.MAX_PACKAGE_BYTES + 1))
        with self.assertRaisesRegex(ota.OTARejected, "size outside bounds"):
            self.verify()

    def test_signer_refuses_source_trust_replacement(self):
        with self.assertRaisesRegex(ValueError, "default source-checkout"):
            signer.write_test_trust(ROOT / "knowco2/ota_trust.py", 1)

    def test_generated_trust_explicitly_insecure(self):
        path = self.root / "knowco2/ota_trust.py"
        signer.write_test_trust(path, 1)
        config = {}
        exec(path.read_text(), config)
        self.assertTrue(config["TEST_ONLY"])
        self.assertEqual(config["PUBLIC_KEY_N_HEX"], self.modulus)
        self.assertIn("ANYONE CAN SIGN", path.read_text())

    def test_stored_extract_is_streamed_and_readback_verified(self):
        self.files["assets/big.txt"] = b"0123456789" * 40000
        signer.write_package(self.files, self.path, 2)
        entry = next(e for e in self.verify()["entries"] if e["name"] == "assets/big.txt")
        target = self.root / "extracted"
        ota.extract_verified_entry(str(self.path), entry, str(target))
        self.assertEqual(target.read_bytes(), self.files[entry["name"]])

    def test_base_build_library_and_abi_must_match_installed_trust(self):
        for field in ("BUILD_ID", "LIBRARY_SET_ID", "MPY_ABI"):
            with self.subTest(field=field):
                old = getattr(self.trust, field)
                setattr(self.trust, field, "different")
                with self.assertRaisesRegex(ota.OTARejected, "base build/dependency"):
                    self.verify()
                setattr(self.trust, field, old)

    def test_compressed_zip_rejected_even_when_payload_matches(self):
        with zipfile.ZipFile(self.path) as archive:
            members = [(i.filename, archive.read(i)) for i in archive.infolist()]
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in members:
                archive.writestr(name, content)
        with self.assertRaisesRegex(ota.OTARejected, "STORED"):
            self.verify()

    def test_whole_nvm_loss_blocks_ota_until_physical_recovery(self):
        plan = self.verify()
        ota.reserve_version(plan, nvm=self.nvm, trust=self.trust)
        ota.complete_version(plan, nvm=self.nvm)
        self.nvm[:] = b"\x00" * len(self.nvm)
        with self.assertRaisesRegex(ota.OTARejected, "blank"):
            self.verify()

    def test_wrong_rsa_key_rejected(self):
        # Keep key length and oddness valid, but change the trusted modulus.
        self.trust.PUBLIC_KEY_N_HEX = format(int(self.modulus, 16) ^ 2, "0512x")
        with self.assertRaisesRegex(ota.OTARejected, "signature verification"):
            self.verify()

    def test_signer_rejects_fatfs_trailing_dot_aliases(self):
        for name in ("knowco2/example.py.", "assets/directory./file.txt", "assets/file."):
            with self.subTest(name=name):
                self.assertFalse(ota.valid_path(name))
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    signer.manifest_for(dict(self.files, **{name: b"x"}), 2)

    def test_crc_only_tampering_rejected_before_reservation(self):
        before = bytes(self.nvm)
        raw = bytearray(self.path.read_bytes())
        entry = next(e for e in ota._zip_entries(str(self.path))
                     if e["name"] == "knowco2/example.py")
        wrong = entry["crc"] ^ 1
        struct.pack_into("<I", raw, entry["local_off"] + 14, wrong)
        central = struct.unpack_from("<I", raw, len(raw) - 22 + 16)[0]
        while raw[central:central + 4] == b"PK\x01\x02":
            n = struct.unpack_from("<H", raw, central + 28)[0]
            if raw[central + 46:central + 46 + n].decode() == entry["name"]:
                struct.pack_into("<I", raw, central + 16, wrong)
                break
            central += 46 + n
        else:
            self.fail("missing directory member")
        self.path.write_bytes(raw)
        with self.assertRaisesRegex(ota.OTARejected, "CRC mismatch"):
            self.verify()
        self.assertEqual(bytes(self.nvm), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
