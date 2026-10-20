"""Trusted synthetic data and fake workers only."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from execution_runner import RunResult, run_patch_job, simulate_patch_job


class RunnerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()
        self.original = b"raise RuntimeError('NEVER EXECUTE')\nvalue = 1\n"
        (self.source / "sample.py").write_bytes(self.original)
        self.edits = [{"file": "sample.py", "original_code": "value = 1", "replacement_code": "value = 2"}]
        self.paths, self.closed, self.profiles = [], [], []

    def factory(self, results):
        results = iter(results)

        @contextmanager
        def create():
            ident = object()
            parent = self

            class Fake:
                def run(self, snapshot, profile):
                    parent.paths.append(snapshot)
                    parent.profiles.append(profile)
                    expected = "value = 1" if len(parent.paths) == 1 else "value = 2"
                    parent.assertIn(expected, (snapshot / "sample.py").read_text())
                    result = next(results)
                    if isinstance(result, BaseException):
                        raise result
                    return result

            try:
                yield Fake()
            finally:
                self.closed.append(ident)
        return create

    def simulate(self, results, **kwargs):
        result = simulate_patch_job(self.source, self.edits, ["sample.py"], self.factory(results), **kwargs)
        self.assertFalse(result["tested"])
        self.assertTrue(result["simulated"])
        self.assertTrue(all(not path.exists() for path in self.paths))
        self.assertEqual((self.source / "sample.py").read_bytes(), self.original)
        return result

    def test_production_disabled_before_any_io(self):
        with patch("execution_runner.tempfile.TemporaryDirectory", side_effect=AssertionError("No copies")), patch("execution_runner.assert_safe_tree", side_effect=AssertionError("No reads")):
            report = run_patch_job("does-not-exist", self.edits, ["sample.py"])
        self.assertEqual(report["execution_status"], "execution_disabled")
        self.assertFalse(report["tested"])
        self.assertFalse(report["simulated"])

    def test_fresh_workers_and_identical_profiles(self):
        report = self.simulate([RunResult("completed", False), RunResult("completed", True)])
        self.assertEqual(report["comparison"], "simulated_failure_to_pass")
        self.assertEqual(len(self.closed), 2)
        self.assertIsNot(self.closed[0], self.closed[1])
        self.assertEqual(self.profiles[0], self.profiles[1])

    def test_regression_and_other_comparisons(self):
        for before, after, expected in [(True, False, "simulated_possible_regression"), (True, True, "simulated_no_selected_regression"), (False, False, "simulated_failures_remain")]:
            with self.subTest(before=before, after=after):
                self.paths.clear()
                report = self.simulate([RunResult("completed", before), RunResult("completed", after)])
                self.assertEqual(report["comparison"], expected)

    def test_invalid_batch_never_starts_worker(self):
        self.edits[0]["replacement_code"] = "value = ("
        report = self.simulate([])
        self.assertEqual(report["execution_status"], "validation_failed")
        self.assertEqual(self.closed, [])

    def test_worker_failures_and_limits(self):
        for status in ["timeout", "resource_limit", "output_limit_exceeded", "dependency_unavailable", "collection_error", "worker_error"]:
            with self.subTest(status=status):
                self.paths.clear()
                report = self.simulate([RunResult(status)])
                self.assertEqual(report["execution_status"], "simulation_" + status)
                self.assertIsNone(report["patched"])

    def test_exception_closes_worker(self):
        report = self.simulate([OSError("private-host-path")])
        self.assertEqual(report["execution_status"], "simulation_worker_error")
        self.assertNotIn("private-host-path", str(report))
        self.assertEqual(len(self.closed), 1)

    def test_second_worker_exception_preserves_baseline(self):
        report = self.simulate([RunResult("completed", True), RuntimeError("failure")])
        self.assertTrue(report["baseline"]["passed"])
        self.assertIsNone(report["patched"])
        self.assertEqual(len(self.closed), 2)

    def test_cancel_before_work(self):
        report = self.simulate([], cancelled=lambda: True)
        self.assertEqual(report["execution_status"], "simulation_cancelled")
        self.assertEqual(self.paths, [])

    def test_cancel_between_workers(self):
        report = self.simulate([RunResult("completed", True)], cancelled=lambda: bool(self.closed))
        self.assertEqual(report["execution_status"], "simulation_cancelled")
        self.assertEqual(len(self.paths), 1)

    def test_invalid_worker_return(self):
        report = self.simulate([{"passed": True}])
        self.assertEqual(report["execution_status"], "simulation_worker_error")

    def test_cancel_exception_still_cleans_up(self):
        with self.assertRaises(KeyboardInterrupt):
            self.simulate([KeyboardInterrupt()])
        self.assertEqual(len(self.closed), 1)
        self.assertTrue(all(not path.exists() for path in self.paths))

    def test_result_cannot_misrepresent_incomplete_run(self):
        for args in [("timeout", True), ("completed", None), ("completed", 1), ("unknown", None)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                RunResult(*args)


if __name__ == "__main__":
    unittest.main()
