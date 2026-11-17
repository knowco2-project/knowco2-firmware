#!/usr/bin/env python3
"""Tests for the locked CircuitPython dependency policy."""

from __future__ import annotations

import copy
from contextlib import ExitStack
from email.message import Message
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "firmware_dependencies.py"
SPEC = importlib.util.spec_from_file_location("firmware_dependencies", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FirmwareDependencyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock = MODULE.load_lock(REPOSITORY_ROOT / "dependencies" / "firmware-build-lock.json")

    def test_checked_in_lock_is_valid(self) -> None:
        MODULE.validate_lock(self.lock)
        self.assertEqual(
            MODULE.calculated_library_set_id(self.lock),
            "cp10.2.1-bundle20260520-3a88e2948952",
        )

    def test_runtime_and_bundle_major_must_match(self) -> None:
        invalid = copy.deepcopy(self.lock)
        invalid["library_bundle"]["major"] = 9
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "bundle major"):
            MODULE.validate_lock(invalid)

    def test_mpy_cross_must_exactly_match_runtime(self) -> None:
        invalid = copy.deepcopy(self.lock)
        invalid["mpy_cross"]["version"] = "10.3.0"
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "exactly match"):
            MODULE.validate_lock(invalid)

    def test_compatibility_manifest_matches_flasher_contract(self) -> None:
        manifest = MODULE.build_compatibility_manifest(self.lock, "v0.0.9")
        self.assertEqual(manifest["schema"], "knowco2.firmware.compatibility.v1")
        self.assertEqual(manifest["firmware_release"], "v0.0.9")
        self.assertEqual(manifest["circuitpython"]["production_tested"], ["10.2.1"])
        self.assertEqual(manifest["source_asset"], "knowco2-dev-v0.0.9.zip")
        self.assertEqual(
            manifest["dependency_lock"]["library_set_id"],
            self.lock["library_bundle"]["library_set_id"],
        )

    def test_build_manifest_carries_immutable_dependency_identity(self) -> None:
        manifest = MODULE.build_release_manifest(
            self.lock,
            "v1.2.3",
            firmware_version="RC-test",
        )
        self.assertEqual(manifest["schema"], "knowco2.firmware.build.v1")
        self.assertEqual(manifest["firmware_version"], "RC-test")
        self.assertEqual(
            manifest["library_bundle"]["vendored_git_tree_sha"],
            "3a88e2948952a8f80d2da6cf5b42854755a8ed3f",
        )
        self.assertEqual(
            manifest["mpy_cross"]["source_commit"],
            "bcfcb511352652d7cb62d3b415e4a624380f1830",
        )
        self.assertEqual(manifest["artifacts"]["source_code"], "knowco2-source-v1.2.3.zip")
        self.assertEqual(manifest["artifacts"]["development"], "knowco2-dev-v1.2.3.zip")

    def test_upstream_release_parser_never_changes_the_lock(self) -> None:
        original = json.dumps(self.lock, sort_keys=True)
        payload = {
            "tag_name": "20260827",
            "target_commitish": "fb489c9d2501c191f670d065fef7078f7bf647b2",
            "html_url": "https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/tag/20260827",
            "assets": [
                {
                    "name": "adafruit-circuitpython-bundle-py-20260827.zip",
                    "digest": "sha256:" + "a" * 64,
                    "browser_download_url": "https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/download/20260827/adafruit-circuitpython-bundle-py-20260827.zip",
                },
                {
                    "name": "adafruit-circuitpython-bundle-10.x-mpy-20260827.zip",
                    "digest": "sha256:" + "b" * 64,
                    "browser_download_url": "https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/download/20260827/adafruit-circuitpython-bundle-10.x-mpy-20260827.zip",
                },
            ],
        }
        report = MODULE.parse_upstream_release(self.lock, payload)
        self.assertTrue(report["update_available"])
        self.assertEqual(report["latest_tag"], "20260827")
        self.assertEqual(json.dumps(self.lock, sort_keys=True), original)

    def test_filesystem_git_tree_hash_matches_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "lib"
            (library / "package").mkdir(parents=True)
            (library / "driver.mpy").write_bytes(bytes((67, 6, 0, 31)) + b"driver")
            (library / "package" / "__init__.mpy").write_bytes(bytes((67, 6, 0, 31)) + b"package")
            executable = library / "helper"
            executable.write_text("helper\n", encoding="utf-8")
            executable.chmod(0o755)

            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(root), "add", "lib"], check=True)
            root_tree = subprocess.run(
                ["git", "-C", str(root), "write-tree"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            expected = subprocess.run(
                ["git", "-C", str(root), "rev-parse", f"{root_tree}:lib"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(MODULE._git_tree_sha(library), expected)

    def test_repository_verifier_checks_actual_snapshot_without_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cp_path = root / "runtime.uf2"
            cp_path.write_bytes(b"UF2 test fixture")
            lib_path = root / "lib-test"
            lib_path.mkdir()
            (lib_path / "foo.mpy").write_bytes(bytes((67, 6, 0, 31)) + b"fixture")
            (root / "THIRD_PARTY_NOTICES.md").write_text(
                "Snapshot: lib-test\nComponent: `foo`\n",
                encoding="utf-8",
            )

            fixture = copy.deepcopy(self.lock)
            fixture["circuitpython"]["checked_in_path"] = "runtime.uf2"
            fixture["circuitpython"]["checked_in_git_blob_sha"] = MODULE._git_blob_sha(cp_path)
            fixture["library_bundle"]["vendored_path"] = "lib-test"
            fixture["library_bundle"]["vendored_git_tree_sha"] = MODULE._git_tree_sha(lib_path)
            fixture["library_bundle"]["expected_top_level"] = ["foo.mpy"]
            fixture["library_bundle"]["library_set_id"] = MODULE.calculated_library_set_id(fixture)

            messages = MODULE.verify_repository(root, fixture)
            self.assertTrue(any("Library set ID" in message for message in messages))

            (lib_path / "foo.mpy").write_bytes(bytes((67, 6, 0, 31)) + b"changed")
            with self.assertRaisesRegex(MODULE.DependencyPolicyError, "Git tree"):
                MODULE.verify_repository(root, fixture)

    def test_github_environment_rejects_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.txt"
            with self.assertRaisesRegex(MODULE.DependencyPolicyError, "newline"):
                MODULE._write_github_pairs(output, {"bad": "first\nsecond"})


class UpstreamMonitorRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = MODULE.load_lock(REPOSITORY_ROOT / "dependencies" / "firmware-build-lock.json")
        self.url = self.lock["monitor"]["release_api"]
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(MODULE.os.environ, {}, clear=True))
        self.opener = MagicMock()
        self.build_opener = self.stack.enter_context(
            patch.object(MODULE.urllib.request, "build_opener", return_value=self.opener)
        )
        self.sleep = self.stack.enter_context(patch.object(MODULE.time, "sleep"))
        self.stack.enter_context(patch.object(MODULE.time, "time", return_value=1000))
        self.stack.enter_context(patch.object(MODULE, "parse_upstream_release", return_value={"ok": True}))
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.geturl.return_value = self.url
        self.response.read.return_value = b"{}"
        self.opener.open.return_value = self.response

    def error(self, code: int, headers=None):
        message = Message()
        for key, value in (headers or {}).items():
            message[key] = value
        return urllib.error.HTTPError(self.url, code, "upstream failure", message, None)

    def test_existing_token_is_attached_only_to_github_api_request(self) -> None:
        MODULE.os.environ["GITHUB_TOKEN"] = "test-token-not-a-real-secret"
        self.assertEqual(MODULE.fetch_upstream_release(self.lock), {"ok": True})
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, self.url)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token-not-a-real-secret")
        self.assertIsInstance(self.build_opener.call_args.args[0], MODULE._GitHubAPIOnlyRedirect)
        self.sleep.assert_not_called()

    def test_local_use_without_token_remains_supported(self) -> None:
        MODULE.fetch_upstream_release(self.lock)
        self.assertIsNone(self.opener.open.call_args.args[0].get_header("Authorization"))

    def test_token_with_embedded_newline_is_rejected_without_logging_it(self) -> None:
        MODULE.os.environ["GITHUB_TOKEN"] = "private-test-token\nInjected: value"
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "contains a newline") as caught:
            MODULE.fetch_upstream_release(self.lock)
        self.assertNotIn("private-test-token", str(caught.exception))
        self.opener.open.assert_not_called()

    def test_response_origin_is_checked_as_defense_in_depth(self) -> None:
        self.response.geturl.return_value = "https://evil.example/"
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "unapproved host"):
            MODULE.fetch_upstream_release(self.lock)
        self.response.read.assert_not_called()
        self.sleep.assert_not_called()

    def test_invalid_json_is_not_retried(self) -> None:
        self.response.read.return_value = b"not-json"
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "invalid JSON"):
            MODULE.fetch_upstream_release(self.lock)
        self.assertEqual(self.opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_unapproved_initial_urls_are_rejected_before_any_request(self) -> None:
        MODULE.os.environ["GITHUB_TOKEN"] = "private-test-token"
        for url in (
            "http://api.github.com/releases/latest",
            "https://api.github.com.evil.example/releases/latest",
            "https://user@api.github.com/releases/latest",
            "https://api.github.com:8443/releases/latest",
            "https://api.github.com:invalid/releases/latest",
        ):
            with self.subTest(url=url):
                self.lock["monitor"]["release_api"] = url
                with self.assertRaisesRegex(MODULE.DependencyPolicyError, "GitHub HTTPS API"):
                    MODULE.fetch_upstream_release(self.lock)
        self.opener.open.assert_not_called()

    def test_cross_origin_and_downgrade_redirects_are_rejected_before_following(self) -> None:
        request = urllib.request.Request(self.url, headers={"Authorization": "Bearer private-test-token"})
        handler = MODULE._GitHubAPIOnlyRedirect()
        for url in ("https://evil.example/", "http://api.github.com/", "https://api.github.com:8443/"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(MODULE.DependencyPolicyError, "unapproved host"):
                    handler.redirect_request(request, None, 302, "Found", {}, url)

    def test_safe_github_api_redirect_keeps_authentication(self) -> None:
        request = urllib.request.Request(self.url, headers={"Authorization": "Bearer private-test-token"})
        redirected = MODULE._GitHubAPIOnlyRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://api.github.com/repositories/123/releases/latest"
        )
        self.assertEqual(redirected.get_header("Authorization"), "Bearer private-test-token")

    def test_primary_rate_limit_waits_until_reset(self) -> None:
        self.opener.open.side_effect = [
            self.error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1005"}),
            self.response,
        ]
        MODULE.fetch_upstream_release(self.lock)
        self.sleep.assert_called_once_with(6)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_retry_after_is_honored(self) -> None:
        self.opener.open.side_effect = [self.error(429, {"Retry-After": "9"}), self.response]
        MODULE.fetch_upstream_release(self.lock)
        self.sleep.assert_called_once_with(9)

    def test_long_reset_fails_without_retrying_early(self) -> None:
        self.opener.open.side_effect = self.error(
            403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4600"}
        )
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "120-second budget"):
            MODULE.fetch_upstream_release(self.lock)
        self.assertEqual(self.opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_rate_limit_without_reset_respects_minimum_minute_and_budget(self) -> None:
        self.opener.open.side_effect = self.error(429)
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "120-second budget"):
            MODULE.fetch_upstream_release(self.lock)
        self.sleep.assert_called_once_with(60)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_permission_denial_is_not_retried(self) -> None:
        self.opener.open.side_effect = self.error(403)
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "HTTP 403 after 1 attempt"):
            MODULE.fetch_upstream_release(self.lock)
        self.sleep.assert_not_called()

    def test_transient_server_failures_stop_after_three_attempts(self) -> None:
        self.opener.open.side_effect = self.error(503)
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "HTTP 503 after 3 attempt"):
            MODULE.fetch_upstream_release(self.lock)
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2])

    def test_network_errors_do_not_expose_token_or_raw_exception(self) -> None:
        MODULE.os.environ["GITHUB_TOKEN"] = "private-test-token"
        self.opener.open.side_effect = urllib.error.URLError("private-test-token")
        with self.assertRaisesRegex(MODULE.DependencyPolicyError, "network error") as caught:
            MODULE.fetch_upstream_release(self.lock)
        self.assertNotIn("private-test-token", str(caught.exception))
        self.assertEqual(self.opener.open.call_count, 3)

    def test_monitor_workflow_passes_existing_token_to_upstream_step(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/dependency-monitor.yml").read_text()
        step = workflow.split("- name: Check official Adafruit bundle release", 1)[1].split("- name:", 1)[0]
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", step)


if __name__ == "__main__":
    unittest.main(verbosity=2)
