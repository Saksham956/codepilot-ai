import io
import unittest
from unittest.mock import Mock, patch

from isolation_probe import CHECKS, DockerProbe, create_args


IMAGE = "sha256:" + "a" * 64
NAME = "codepilot-probe-" + "b" * 32


class ProbeTests(unittest.TestCase):
    def test_launch_flags_and_no_host_shares(self):
        args = create_args(IMAGE, NAME, CHECKS["permissions"])
        for flag in ["--pull=never", "--network=none", "--read-only", "--user=65534:65534",
                     "--cap-drop=ALL", "--memory=512m", "--memory-swap=512m",
                     "--pids-limit=64", "--cpus=1", "--log-driver=none"]:
            self.assertIn(flag, args)
        self.assertFalse(any(arg.startswith(("--mount", "--volume", "--privileged", "--env-file")) for arg in args))
        self.assertEqual(args[-5:-1], [IMAGE, "-I", "-B", "-c"])

    def test_no_tag_or_arbitrary_container_names(self):
        for image, name in [("python:latest", NAME), (IMAGE, "existing-user-container"), ("--help", NAME)]:
            with self.subTest(image=image, name=name), self.assertRaises(ValueError):
                create_args(image, name, "pass")

    def test_unavailable_engine_never_creates(self):
        probe = DockerProbe("docker")
        probe.call = Mock(return_value=Mock(returncode=1, stdout=b""))
        self.assertEqual(probe.run(IMAGE)["status"], "runtime_unavailable")
        self.assertEqual(probe.call.call_count, 1)

    def configured(self):
        probe = DockerProbe("docker")
        probe.inspect = Mock(side_effect=[{
            "Image": IMAGE, "Config": {"User": "65534:65534"}, "Mounts": [],
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True,
                "Memory": 536870912, "MemorySwap": 536870912, "PidsLimit": 64,
                "NanoCpus": 1_000_000_000, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"]},
        }, {"State": {"ExitCode": 0, "OOMKilled": False}}])
        probe.call = Mock(return_value=Mock(returncode=0, stdout=b""))
        return probe

    def test_completed_probe_verified_cleanup(self):
        probe = self.configured()
        process = Mock(stdout=io.BytesIO(b"fixed result"))
        process.poll.return_value = 0
        with patch("isolation_probe.subprocess.Popen", return_value=process):
            report = probe.one(IMAGE, "permissions")
        self.assertTrue(report["passed"])
        self.assertTrue(report["cleanup_verified"])
        self.assertEqual(probe.call.call_args_list[-2].args[:2], ("rm", "--force"))
        self.assertTrue(probe.call.call_args_list[-2].args[2].startswith("codepilot-probe-"))

    def test_configuration_failure_never_starts(self):
        probe = self.configured()
        probe.inspect.side_effect = [{"Image": "wrong"}]
        with patch("isolation_probe.subprocess.Popen") as launch:
            report = probe.one(IMAGE, "permissions")
        launch.assert_not_called()
        self.assertFalse(report["passed"])
        self.assertTrue(report["cleanup_verified"])

    def test_cleanup_failure_cannot_pass(self):
        probe = self.configured()
        probe.call.side_effect = [Mock(returncode=0), Mock(returncode=1), Mock(returncode=0, stdout=b"")]
        process = Mock(stdout=io.BytesIO(b""))
        process.poll.return_value = 0
        with patch("isolation_probe.subprocess.Popen", return_value=process):
            report = probe.one(IMAGE, "permissions")
        self.assertFalse(report["passed"])
        self.assertFalse(report["cleanup_verified"])

    def test_launch_failure_still_removes_owned_container(self):
        probe = self.configured()
        with patch("isolation_probe.subprocess.Popen", side_effect=OSError("startup")):
            report = probe.one(IMAGE, "permissions")
        self.assertFalse(report["passed"])
        self.assertTrue(report["cleanup_verified"])


if __name__ == "__main__":
    unittest.main()
