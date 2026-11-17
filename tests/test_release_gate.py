"""A green engineering CI run must not authorize customer publication."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import release_gate

class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads((ROOT / 'docs/releases/1.0.0-gates.json').read_text())

    def test_integration_cannot_be_released(self):
        self.assertTrue(release_gate.unresolved(self.ledger))
        result = subprocess.run([sys.executable, str(ROOT / 'tools/release_gate.py'), '--enforce'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_report_is_not_release_approval(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/release_gate.py'), '--report'], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'BLOCKED', result.stdout)

    def test_missing_gate_is_error_not_implicit_pass(self):
        self.ledger['gates'].pop('production_signing')
        with self.assertRaises(ValueError): release_gate.unresolved(self.ledger)

    def test_passing_gate_requires_evidence(self):
        self.ledger['gates']['production_signing']['status'] = 'passed'
        with self.assertRaises(ValueError): release_gate.unresolved(self.ledger)

    def test_malformed_status_or_truthy_string_is_not_authorization(self):
        for change in ('authorization', 'status'):
            doc = copy.deepcopy(self.ledger)
            if change == 'authorization': doc['release_allowed'] = 'true'
            else: doc['gates']['production_signing']['status'] = True
            with self.assertRaises(ValueError): release_gate.unresolved(doc)

if __name__ == '__main__': unittest.main(verbosity=2)
