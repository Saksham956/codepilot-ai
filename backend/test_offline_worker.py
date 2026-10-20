import copy
import json
from pathlib import Path
import unittest
from validate_offline_worker import check_runtime


class RuntimeChecks(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((Path(__file__).parent / 'requests_harness_inputs/requests_harness_dependency_lock.json').read_text())
        self.receipt = dict(prefix='/opt/worker-venv', requests_discoverable=False,
                            environment=dict(self.lock['target']),
                            distributions=[dict(name=r['name'], version=r['version']) for r in self.lock['dependencies']])

    def test_matching_runtime(self):
        self.assertTrue(check_runtime(self.lock, self.receipt))

    def test_rejects_drift(self):
        variants = []
        for key, value in [('prefix', '/usr/local'), ('requests_discoverable', True)]:
            row = copy.deepcopy(self.receipt)
            row[key] = value
            variants.append(row)
        row = copy.deepcopy(self.receipt)
        row['environment']['sys_platform'] = 'win32'
        variants.append(row)
        row = copy.deepcopy(self.receipt)
        row['distributions'][0]['version'] = '0.0'
        variants.append(row)
        row = copy.deepcopy(self.receipt)
        row['distributions'].append(dict(name='requests', version='2.32.5'))
        variants.append(row)
        for receipt in variants:
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                check_runtime(self.lock, receipt)

    def test_actual_python_constraints(self):
        self.lock['dependencies'][0]['requires_python'] = '>=99'
        with self.assertRaises(ValueError):
            check_runtime(self.lock, self.receipt)

    def test_actual_marker_closure(self):
        self.lock['dependencies'][0]['requires_dist'] = ['missing-package; sys_platform == "linux"']
        with self.assertRaises(ValueError):
            check_runtime(self.lock, self.receipt)
