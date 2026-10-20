import copy
import json
from pathlib import Path
import unittest
from assess_real_auth_compatibility import assess


class AssessmentChecks(unittest.TestCase):
    def setUp(self):
        self.measurement = json.loads(Path(__file__).with_name('real_auth_matrix_results.json').read_text())

    def test_decision_rejects_unsafe_defaults(self):
        report = assess(self.measurement)
        self.assertEqual(len(report['unconditional_utf8_changes_existing_headers']), 3)
        self.assertEqual(len(report['per_field_fallback_mixed_encoding_cases']), 2)
        self.assertFalse(report['proposed_fallback_approved'])
        self.assertFalse(report['candidate_patch_executed'])

    def test_changed_measurement_rejected(self):
        self.measurement['result']['compatibility_matrix'][0]['observed']['authorization'] = 'changed'
        with self.assertRaises(ValueError):
            assess(self.measurement)

    def test_incomplete_matrix_rejected(self):
        self.measurement['result']['compatibility_matrix'].pop()
        with self.assertRaises(ValueError):
            assess(self.measurement)

    def test_other_artifact_rejected(self):
        self.measurement['artifact_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            assess(self.measurement)
