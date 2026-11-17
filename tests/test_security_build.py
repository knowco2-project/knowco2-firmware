"""Security-test artifact isolation, explicit trust and no-release guardrails."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import build_device_image as image
from tools import build_security_test as build
from tools import run_tests, sign_ota


class SecurityBuildTests(unittest.TestCase):
    def trust_source(self, root, key="", test_only=False, key_id="unprovisioned"):
        path = root / "knowco2/ota_trust.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PUBLIC_KEY_N_HEX = %r\nKEY_ID = %r\nTEST_ONLY = %r\nMIN_SEQUENCE = 0\nBUILD_ID = \"0000000000000000000000000000000000000000\"\nLIBRARY_SET_ID = \"cp10.2.1-bundle20260520-3a88e2948952\"\nMPY_ABI = \"C6.0.31\"\n" %
                        (key, key_id, test_only))
        return path

    def test_default_source_trust_is_not_a_test_key(self):
        image.validate_ota_trust(ROOT)

    def test_public_fixture_is_blocked_even_with_false_label(self):
        modulus = sign_ota.test_modulus()
        self.assertEqual(hashlib.sha256(bytes.fromhex(modulus)).hexdigest(),
                         image.PUBLIC_TEST_MODULUS_SHA256)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for key in (modulus, modulus.upper()):
                self.trust_source(root, key=key, test_only=False, key_id="looks-production")
                for allow in (False, True):
                    with self.assertRaisesRegex(ValueError, "masquerade"):
                        image.validate_ota_trust(root, allow)
            self.trust_source(root, key=modulus, test_only=True)
            with self.assertRaisesRegex(ValueError, "forbidden"):
                image.validate_ota_trust(root)
            image.validate_ota_trust(root, allow_test_trust=True)

    def test_test_label_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.trust_source(root, test_only=True)
            with self.assertRaises(ValueError):
                image.validate_ota_trust(root)
            image.validate_ota_trust(root, allow_test_trust=True)

    def test_dynamic_duplicate_missing_or_nonboolean_trust_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for suffix in ("TEST_ONLY = False\n", "import os\n", "PUBLIC_KEY_N_HEX = str()\n"):
                path = self.trust_source(root)
                path.write_text(path.read_text() + suffix)
                with self.assertRaises(ValueError):
                    image.validate_ota_trust(root)
            path = self.trust_source(root)
            path.write_text(path.read_text().replace("TEST_ONLY = False", "TEST_ONLY = 0"))
            with self.assertRaises(ValueError):
                image.validate_ota_trust(root)
            path.unlink()
            with self.assertRaises(ValueError):
                image.validate_ota_trust(root)

    def test_source_package_excludes_private_fixture_tools_tests_and_bytecode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            build.copy_source(source)
            archive = root / "source.zip"
            build.zip_tree(source, archive)
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
                self.assertIn("code.py", names)
                self.assertIn("boot.py", names)
                for name in names:
                    self.assertFalse(name.startswith(("tests/", "tools/", ".github/", "lib/")), name)
                    self.assertNotIn("__pycache__", name)
                    self.assertFalse(name.endswith((".pyc", ".pyo", ".pem")), name)
                    self.assertNotIn(b"BEGIN PRIVATE KEY", zf.read(name))

    def test_allocation_accounts_for_fat_cluster_overhead(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "directory").mkdir()
            (root / "directory/tiny").write_bytes(b"x")
            (root / "large").write_bytes(b"x" * (build.CLUSTER + 1))
            self.assertEqual(build.allocation(root), 4 * build.CLUSTER)

    def test_runner_discovers_every_suite_and_root_mock_tests(self):
        discovered = run_tests.test_paths(ROOT)
        expected = set((ROOT / "tests").glob("test_*.py"))
        self.assertEqual(set(discovered), expected | {
            ROOT / "test_build_device_image.py", ROOT / "test_sensors.py"})
        self.assertEqual(len(discovered), len(set(discovered)))
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("python3 tools/run_tests.py", ci)
        self.assertNotIn("test -w /", ci)

    def test_security_artifact_workflow_cannot_publish_a_release(self):
        workflow = (ROOT / ".github/workflows/security-test.yml").read_text()
        self.assertIn("contents: read", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        self.assertIn("codebuild-knowco2-firmware-", workflow)
        for prohibited in ("contents: write", "action-gh-release", "gh release", "git push", "dependency-monitor.yml"):
            self.assertNotIn(prohibited, workflow)
        self.assertIn("--test-key", (ROOT / "tools/build_security_test.py").read_text())

    def test_production_release_workflow_is_fail_closed(self):
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


if __name__ == "__main__":
    unittest.main()
