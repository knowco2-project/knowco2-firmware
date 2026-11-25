"""Contracts visible to inventory, Home Assistant, USB clients and bench operators."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowco2 import ids, state, version


class ReadinessTest(unittest.TestCase):
    def test_same_board_keeps_application_hwid_and_hostname(self):
        board = SimpleNamespace(board_id="adafruit_feather_esp32s3_reverse_tft")
        legacy = bytes.fromhex("201a2b3c4d5e")
        canonical = bytes.fromhex("02a1b2c3d4e5")
        for tag, uid in (("10.2.1", legacy), ("10.3.2", legacy), ("11.0.0-alpha.1", legacy),
                         ("11.0.0-alpha.2", canonical), ("11.0.0-beta.0", canonical)):
            mcu = SimpleNamespace(cpu=SimpleNamespace(uid=uid))
            with self.subTest(tag=tag), patch.object(ids, "board", board), \
                    patch.object(ids, "microcontroller", mcu), patch.object(version, "CP_VERSION", tag):
                ids.init_ids()
                ids.init_mdns_hostname()
                self.assertEqual(state.hwid_hex, "201A2B3C4D5E")
                self.assertEqual(state.mdns_hostname, "knowco2-4d5e")

    def test_no_rewriting_other_boards_or_accepting_failed_efuse_read(self):
        self.assertEqual(ids._application_hwid(bytes.fromhex("02abcd123456"), "other", "11.0.0-beta.0"),
                         "02ABCD123456")
        self.assertIsNone(ids._application_hwid(bytes(6), "adafruit_feather_esp32s3_reverse_tft",
                                               "11.0.0-beta.0"))

    def test_operator_can_distinguish_beta_from_final(self):
        for tag in ("10.2.1", "11.0.0-beta.0", "11.0.0"):
            impl = SimpleNamespace(name="circuitpython", version=(11, 0, 0, "preview"))
            runtime = SimpleNamespace(implementation=impl, version="3.4.0; CircuitPython " + tag + " on 2026-11-24")
            with self.subTest(tag=tag), patch.object(version, "_sys", runtime):
                self.assertEqual(version.detect_circuitpython_version(), tag)


if __name__ == "__main__":
    unittest.main()
