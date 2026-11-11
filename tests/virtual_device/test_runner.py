from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from model import InputDevice, read_hold_config
from run import build_report, execute

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("scenarios.json")


class RunnerTests(unittest.TestCase):
    def test_parallel_and_serial_replays_are_identical(self):
        serial = build_report(ROOT, FIXTURE, workers=1)
        parallel = build_report(ROOT, FIXTURE, workers=4)
        self.assertEqual(serial["results"], parallel["results"])
        self.assertEqual(serial["failed"], 0)
        self.assertEqual(serial["passed"], 9)
        self.assertFalse(serial["release_qualified"])
        self.assertTrue(all(r["product_acceptance"] == "not_implemented" for r in serial["results"]))

    def test_missing_case_is_an_error(self):
        with self.assertRaises(ValueError): build_report(ROOT, FIXTURE, case_id="missing")

    def test_invalid_worker_count_is_an_error(self):
        with self.assertRaises(ValueError): build_report(ROOT, FIXTURE, workers=0)

    def test_missing_or_nonliteral_config_is_an_error(self):
        with self.assertRaises(ValueError): read_hold_config("LP_A_HOLD_SECONDS = 2\n")
        with self.assertRaises((ValueError, TypeError)):
            read_hold_config("LP_A_HOLD_SECONDS = float('nan')\n")

    def test_oracle_catches_deliberate_gesture_mutation(self):
        # Deliberately damage the candidate, not the expected result. The bench
        # must flag the difference rather than deriving expectations from it.
        source = (ROOT / "knowco2/buttons.py").read_text()
        old = 'B_SHORT = "b_short"'
        self.assertEqual(source.count(old), 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "knowco2").mkdir()
            (root / "knowco2/buttons.py").write_text(source.replace(old, 'B_SHORT = "broken"'))
            (root / "knowco2/config.py").write_bytes((ROOT / "knowco2/config.py").read_bytes())
            case = json.loads(FIXTURE.read_text())["cases"][0]
            result = execute(root, case)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["actual_actions"], ["broken"])


if __name__ == "__main__":
    unittest.main()
