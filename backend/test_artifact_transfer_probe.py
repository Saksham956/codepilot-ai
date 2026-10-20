import json
import unittest
from unittest.mock import Mock, patch

from artifact_transfer_probe import PINNED_IMAGE, receiver_script, run_synthetic_transfer
from source_artifact import verify_source_artifact


class TransferProbeTests(unittest.TestCase):
    def configured(self):
        docker = Mock(command=["docker", "--context", "desktop-linux"])
        docker.inspect.side_effect = [
            {"Os": "linux", "Config": {}},
            {"Image": PINNED_IMAGE, "Config": {"User": "65534:65534"}, "Mounts": [],
             "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True}},
            {"State": {"ExitCode": 0}},
        ]
        docker.call.return_value = Mock(returncode=0, stdout=b"")
        return docker

    def receipt(self, *args, **kwargs):
        self.assertNotIn("shell", kwargs)
        import hashlib
        payload = kwargs["input"]
        manifest = verify_source_artifact(payload, hashlib.sha256(payload).hexdigest())
        self.assertEqual([item["path"] for item in manifest["files"]], ["sample.py"])
        return Mock(returncode=0, stdout=json.dumps(manifest).encode())

    def test_fixed_receiver_and_digest_validation(self):
        script = receiver_script("0" * 64)
        compile(script, "trusted_receiver", "exec")
        self.assertNotIn("extractall", script)
        for digest in ["bad", "';raise Exception()", None]:
            with self.assertRaises(ValueError):
                receiver_script(digest)

    def test_manifest_receipt_and_cleanup(self):
        docker = self.configured()
        with patch("artifact_transfer_probe.DockerProbe", return_value=docker), patch("subprocess.run", side_effect=self.receipt):
            report = run_synthetic_transfer("docker")
        self.assertEqual(report["status"], "synthetic_transfer_passed")
        self.assertFalse(report["source_executed"])
        self.assertFalse(report["repository_execution_enabled"])
        self.assertTrue(report["cleanup_verified"])
        create = docker.call.call_args_list[0].args
        self.assertIn("--interactive", create)
        self.assertIn(PINNED_IMAGE, create)
        self.assertFalse(any(arg.startswith(("--mount", "--volume")) for arg in create))

    def test_modified_receipt_fails_but_cleans_up(self):
        docker = self.configured()
        with patch("artifact_transfer_probe.DockerProbe", return_value=docker), patch("subprocess.run", return_value=Mock(returncode=0, stdout=b"{}")):
            report = run_synthetic_transfer("docker")
        self.assertEqual(report["status"], "probe_failed")
        self.assertTrue(report["cleanup_verified"])

    def test_timeout_or_cleanup_failure_cannot_pass(self):
        import subprocess
        for failure in ("timeout", "cleanup"):
            docker = self.configured()
            if failure == "cleanup":
                docker.call.side_effect = [Mock(returncode=0), Mock(returncode=1), Mock(returncode=0, stdout=b"")]
            effect = subprocess.TimeoutExpired("synthetic", 30) if failure == "timeout" else self.receipt
            with self.subTest(failure=failure), patch("artifact_transfer_probe.DockerProbe", return_value=docker), patch("subprocess.run", side_effect=effect):
                report = run_synthetic_transfer("docker")
            self.assertEqual(report["status"], "probe_failed")


if __name__ == "__main__":
    unittest.main()
