import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from execution_runner import run_patch_job
from synthetic_worker_job import (
    BASELINE, PATCHED, TRUSTED_HASHES, PINNED_IMAGE,
    fixture_worker_script, run_synthetic_job, _run_fixture_worker,
)


class SyntheticJobTests(unittest.TestCase):
    def setUp(self):
        self.docker = Mock(command=["docker"])
        self.docker.inspect.return_value = {"Os": "linux", "Config": {}}

    def test_two_snapshots_comparison_and_cleanup(self):
        artifacts = []

        def worker(docker, artifact):
            self.assertIs(docker, self.docker)
            artifacts.append(artifact)
            return {"status": "completed", "passed": len(artifacts) == 2, "cleanup_verified": True}
        with patch("synthetic_worker_job.DockerProbe", return_value=self.docker), patch("synthetic_worker_job._run_fixture_worker", side_effect=worker):
            report = run_synthetic_job("docker")
        self.assertEqual(report["status"], "synthetic_job_completed")
        self.assertEqual(report["comparison"], "synthetic_failure_to_pass")
        self.assertTrue(report["host_cleanup_verified"])
        self.assertTrue(report["synthetic_tested"])
        self.assertFalse(report["tested"])
        self.assertNotEqual(artifacts[0].sha256, artifacts[1].sha256)
        self.assertEqual([item["path"] for item in artifacts[0].manifest()["files"]], ["calculator.py"])

    def test_cancel_before_runtime_access(self):
        with patch("synthetic_worker_job.DockerProbe") as runtime:
            report = run_synthetic_job("docker", cancelled=lambda: True)
        runtime.assert_not_called()
        self.assertEqual(report["status"], "cancelled")

    def test_cancel_between_workers(self):
        calls = []

        def worker(*args):
            calls.append(args)
            return {"status": "completed", "passed": False}
        with patch("synthetic_worker_job.DockerProbe", return_value=self.docker), patch("synthetic_worker_job._run_fixture_worker", side_effect=worker):
            report = run_synthetic_job("docker", cancelled=lambda: bool(calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["host_cleanup_verified"])
        self.assertFalse(report["synthetic_tested"])

    def test_timeout_stops_after_baseline_and_cleans_host(self):
        with patch("synthetic_worker_job.DockerProbe", return_value=self.docker), patch("synthetic_worker_job._run_fixture_worker", return_value={"status": "timeout"}) as worker:
            report = run_synthetic_job("docker")
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(report["status"], "timeout")
        self.assertTrue(report["host_cleanup_verified"])
        self.assertEqual(report["comparison"], "not_run")

    def test_unavailable_runtime_fails_closed(self):
        self.docker.inspect.side_effect = RuntimeError("unavailable")
        with patch("synthetic_worker_job.DockerProbe", return_value=self.docker), patch("synthetic_worker_job._run_fixture_worker") as worker:
            report = run_synthetic_job("docker")
        worker.assert_not_called()
        self.assertEqual(report["status"], "job_error")
        self.assertFalse(report["tested"])

    def test_generated_script_guards_exact_trusted_fixtures(self):
        script = fixture_worker_script("a" * 64)
        compile(script, "trusted_worker", "exec")
        self.assertIn(repr(TRUSTED_HASHES), script)
        self.assertLess(script.index("not in TRUSTED_HASHES"), script.index("runpy.run_path"))
        for invalid in ("bad", None, "'; raise Exception()"):
            with self.assertRaises(ValueError):
                fixture_worker_script(invalid)

    def test_production_still_disabled(self):
        self.assertEqual(run_patch_job(None, None, None)["execution_status"], "execution_disabled")


class WorkerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.docker = Mock(command=["docker"])
        self.docker.call.return_value = Mock(returncode=0, stdout=b"")
        self.config = {"Image": PINNED_IMAGE, "Config": {"User": "65534:65534"}, "Mounts": [],
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Memory": 536870912,
                "MemorySwap": 536870912, "PidsLimit": 64, "NanoCpus": 1000000000,
                "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"]}}
        self.docker.inspect.side_effect = [self.config, {"State": {"ExitCode": 0, "OOMKilled": False}}]
        self.artifact = Mock(payload=b"synthetic", sha256="a" * 64)
        self.manifest = {"files": [{"path": "calculator.py"}]}

    def run_case(self, result=None, error=None):
        with patch("synthetic_worker_job.fixture_worker_script", return_value="trusted receiver"), patch("synthetic_worker_job.policy.verify_source_artifact", return_value=self.manifest), patch("synthetic_worker_job.subprocess.run", side_effect=error, return_value=result):
            return _run_fixture_worker(self.docker, self.artifact)

    def test_good_receipt(self):
        receipt = {"manifest": self.manifest, "synthetic_passed": False, "materialization_cleanup_verified": True}
        report = self.run_case(Mock(returncode=0, stdout=json.dumps(receipt).encode()))
        self.assertEqual(report["status"], "completed")
        self.assertFalse(report["passed"])
        self.assertTrue(report["cleanup_verified"])

    def test_mismatched_receipt_rejected(self):
        report = self.run_case(Mock(returncode=0, stdout=b"{}"))
        self.assertEqual(report["status"], "worker_error")
        self.assertTrue(report["cleanup_verified"])

    def test_bad_configuration_never_starts(self):
        self.config["HostConfig"]["NetworkMode"] = "host"
        with patch("synthetic_worker_job.fixture_worker_script", return_value="trusted receiver"), patch("synthetic_worker_job.policy.verify_source_artifact", return_value=self.manifest), patch("synthetic_worker_job.subprocess.run") as launch:
            report = _run_fixture_worker(self.docker, self.artifact)
        launch.assert_not_called()
        self.assertEqual(report["status"], "worker_error")

    def test_timeout_and_failed_cleanup(self):
        report = self.run_case(error=subprocess.TimeoutExpired("trusted", 30))
        self.assertEqual(report["status"], "timeout")
        self.docker.call.side_effect = [Mock(returncode=0), Mock(returncode=1), Mock(returncode=0, stdout=b"")]
        self.docker.inspect.side_effect = [self.config]
        report = self.run_case(error=subprocess.TimeoutExpired("trusted", 30))
        self.assertEqual(report["status"], "cleanup_error")


if __name__ == "__main__":
    unittest.main()
