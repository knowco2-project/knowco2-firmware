#!/usr/bin/env python3
"""Real signed OTA installer integration in a temporary filesystem (never /)."""
import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_provisioning import routes, state
from knowco2 import ota_security as ota
from tools import sign_ota as signer


class SignedInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modulus = signer.test_modulus()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.device = self.root / "device"
        self.device.mkdir()
        self.path = self.root / "upload.zip"
        self.trust = types.SimpleNamespace(PUBLIC_KEY_N_HEX=self.modulus,
            KEY_ID=signer.TEST_KEY_ID, TEST_ONLY=True, MIN_SEQUENCE=1,
            BUILD_ID="0" * 40, LIBRARY_SET_ID=signer.LIBRARY_SET_ID,
            MPY_ABI="C6.0.31")
        self.nvm = bytearray(256)
        self.results = []
        self.reset = []
        self.files = {"boot.py": b"# new boot\n", "code.py": b"# new app\n",
                      "knowco2/version.py": b'FIRMWARE_VERSION = "TEST-2"\n',
                      "knowco2/example.mpy": b"C\x06\x00\x1f" + b"z" * 60000,
                      "assets/test.txt": b"test-only\n"}
        for name, data in {"code.py": b"# old app\n", "boot.py": b"# old boot\n",
                           "knowco2/example.py": b"# stale source alias\n"}.items():
            dest = self.device / name
            dest.parent.mkdir(exist_ok=True, parents=True)
            dest.write_bytes(data)
        self.before = self.snapshot()
        for mock in (
            patch.object(routes, "_OTA_FS_ROOT", str(self.device)),
            patch.object(routes, "_send_ota_result", side_effect=lambda c, ok, msg: self.results.append((ok, msg))),
            patch.object(routes.time, "sleep", return_value=None),
            patch.object(routes, "microcontroller", types.SimpleNamespace(reset=lambda: self.reset.append(1))),
            patch.object(routes, "_fs_free_bytes", return_value=10000000),
            patch.object(ota, "ota_trust", self.trust),
            patch.dict(sys.modules, {"microcontroller": types.SimpleNamespace(nvm=self.nvm)}),
        ):
            mock.start()
            self.addCleanup(mock.stop)
        state._wd = None
        ota.initialize_baseline(nvm=self.nvm, trust=self.trust)
        self.package()

    def snapshot(self):
        return {str(p.relative_to(self.device)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.device.rglob("*") if p.is_file() and "ota_staged" not in p.parts}

    def package(self, sequence=2):
        signer.write_package(self.files, self.path, sequence)

    def install(self):
        routes._process_zip_update(None, str(self.path))
        return self.results[-1]

    def test_signed_install_readback_alias_removal_and_persistent_replay(self):
        ok, msg = self.install()
        self.assertTrue(ok, msg)
        self.assertIn("SHA-256", msg)
        self.assertIn("TEST ONLY", msg)
        for name, data in self.files.items():
            self.assertEqual((self.device / name).read_bytes(), data)
        self.assertEqual((self.device / "code.py.bak").read_bytes(), b"# old app\n")
        self.assertFalse((self.device / "knowco2/example.py").exists())
        self.assertFalse((self.device / "ota_staged").exists())
        self.assertFalse(self.path.exists())
        self.assertEqual(self.reset, [1])
        completed = self.snapshot()
        self.package()
        self.assertFalse(self.install()[0])
        self.assertEqual(self.snapshot(), completed)
        self.package(sequence=1)
        self.assertFalse(self.install()[0])
        self.assertEqual(self.snapshot(), completed)

    def test_tampering_rejected_before_any_live_file_change_or_reservation(self):
        original_nvm = bytes(self.nvm)
        raw = bytearray(self.path.read_bytes())
        with zipfile.ZipFile(self.path) as zf:
            info = zf.getinfo("knowco2/example.mpy")
            offset = info.header_offset
        name_len = int.from_bytes(raw[offset+26:offset+28], "little")
        extra_len = int.from_bytes(raw[offset+28:offset+30], "little")
        raw[offset+30+name_len+extra_len+10] ^= 1
        self.path.write_bytes(raw)
        ok, msg = self.install()
        self.assertFalse(ok, msg)
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(bytes(self.nvm), original_nvm)
        self.assertFalse(self.reset)

    def test_truncation_and_unsigned_package_rejected(self):
        self.path.write_bytes(self.path.read_bytes()[:100])
        self.assertFalse(self.install()[0])
        with zipfile.ZipFile(self.path, "w") as zf:
            for name, data in self.files.items():
                zf.writestr(name, data)
        self.assertFalse(self.install()[0])
        self.assertEqual(self.snapshot(), self.before)

    def test_staged_flash_hash_failure_never_reserves_or_commits(self):
        original_nvm = bytes(self.nvm)
        with patch.object(ota, "verify_staged_file", side_effect=ota.OTARejected("flash changed")):
            self.assertFalse(self.install()[0])
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(bytes(self.nvm), original_nvm)
        self.assertFalse((self.device / "ota_staged").exists())

    def test_low_space_fails_before_reservation(self):
        original_nvm = bytes(self.nvm)
        with patch.object(routes, "_fs_free_bytes", return_value=1000):
            self.assertFalse(self.install()[0])
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(bytes(self.nvm), original_nvm)

    def test_low_space_does_not_fall_back_to_live_per_file_install(self):
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

    def test_reservation_failure_leaves_live_files_unchanged(self):
        with patch.object(ota, "reserve_version", side_effect=ota.OTARejected("NVM write failed")):
            self.assertFalse(self.install()[0])
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse(self.reset)

    def test_stale_staging_is_removed_after_verified_package(self):
        junk = self.device / "ota_staged/old/junk.py"
        junk.parent.mkdir(parents=True)
        junk.write_bytes(b"incomplete")
        self.assertTrue(self.install()[0])
        self.assertFalse((self.device / "ota_staged").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
