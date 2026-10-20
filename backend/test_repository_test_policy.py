import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from execution_runner import run_patch_job
from repository_test_policy import (
    REVIEWED_REPOSITORY_PROFILES, dependency_lock_digest, preflight_repository_plan,
)
from source_artifact import build_source_artifact


class RepositoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        root = Path(self.owner.name)
        (root / "tests").mkdir()
        (root / "source.py").write_text("raise RuntimeError('NEVER IMPORT')\n")
        (root / "tests/test_sample.py").write_text("raise RuntimeError('NEVER COLLECT')\n")
        self.artifact = build_source_artifact(root, ["source.py", "tests/test_sample.py"])
        self.plan = {"schema_version": 1, "repository": "https://github.com/example/fixture",
            "revision": "a" * 40, "artifact_sha256": self.artifact.sha256,
            "worker_image": "sha256:" + "b" * 64,
            "dependencies": [{"name": "pytest", "version": "0.0.0", "wheel_sha256": "c" * 64}],
            "test_nodes": ["tests/test_sample.py::TestExample::test_value"]}
        self.relock()

    def relock(self):
        self.plan["dependency_lock_sha256"] = dependency_lock_digest(self.plan["dependencies"])

    def check(self):
        report = preflight_repository_plan(self.plan, self.artifact.payload, self.artifact.sha256)
        self.assertFalse(report["eligible_for_execution"])
        self.assertFalse(report["repository_execution_enabled"])
        self.assertFalse(report["tested"])
        self.assertEqual(report["execution_status"], "execution_disabled")
        return report

    def test_structurally_valid_is_still_blocked(self):
        report = self.check()
        self.assertTrue(report["plan_valid"])
        self.assertEqual(report["review_status"], "not_reviewed")
        self.assertEqual(len(report["blockers"]), 5)
        self.assertEqual(report["selected_test_count"], 1)

    def test_no_reviewed_profiles_or_production_enablement(self):
        self.assertEqual(dict(REVIEWED_REPOSITORY_PROFILES), {})
        with self.assertRaises(TypeError):
            REVIEWED_REPOSITORY_PROFILES["fake"] = True
        self.assertEqual(run_patch_job(None, None, None)["execution_status"], "execution_disabled")

    def test_commands_and_self_approval_flags_rejected(self):
        for field in ("command", "approved", "enable_execution", "install_command", "pytest_args", "plugins", "env"):
            with self.subTest(field=field):
                self.plan[field] = True
                self.assertFalse(self.check()["plan_valid"])
                del self.plan[field]

    def test_malformed_top_level_fields(self):
        original = self.plan
        for invalid in (None, [], {}, "plan"):
            self.plan = invalid
            self.assertFalse(self.check()["plan_valid"])
        self.plan = original

    def test_repository_url_boundaries(self):
        for url in ("file:///tmp/repo", "https://github.com@evil.test/a/b", "https://github.com/a/b?token=private", "https://github.com/a/b.git", "https://github.com/../b"):
            with self.subTest(url=url):
                self.plan["repository"] = url
                self.assertFalse(self.check()["plan_valid"])

    def test_revision_is_full_object_id(self):
        for revision in ("main", "v2.32.5", "abc123", "A" * 40, 123):
            with self.subTest(revision=revision):
                self.plan["revision"] = revision
                self.assertFalse(self.check()["plan_valid"])

    def test_mutable_image_tag_rejected(self):
        for image in ("python:latest", "python:3.13-slim", "sha256:short", "b" * 64):
            self.plan["worker_image"] = image
            self.assertFalse(self.check()["plan_valid"])

    def test_artifact_tampering_and_identity_mismatch(self):
        self.plan["artifact_sha256"] = "0" * 64
        self.assertFalse(self.check()["plan_valid"])
        report = preflight_repository_plan(self.plan, self.artifact.payload + b"x", self.artifact.sha256)
        self.assertFalse(report["plan_valid"])

    def test_dependency_version_ranges_urls_and_editable_rejected(self):
        for version in (">=1.0", "~=1.0", "latest", "https://example.test/pkg", "-e .", "./local"):
            self.plan["dependencies"][0]["version"] = version
            self.relock()
            self.assertFalse(self.check()["plan_valid"])

    def test_dependency_hash_and_duplicate_rejected(self):
        self.plan["dependencies"][0]["wheel_sha256"] = "missing"
        self.relock()
        self.assertFalse(self.check()["plan_valid"])
        self.plan["dependencies"][0]["wheel_sha256"] = "c" * 64
        self.plan["dependencies"] *= 2
        self.relock()
        self.assertFalse(self.check()["plan_valid"])

    def test_dependency_lock_must_match_pins(self):
        self.plan["dependencies"][0]["version"] = "0.0.1"
        self.assertFalse(self.check()["plan_valid"])

    def test_pytest_must_be_declared(self):
        self.plan["dependencies"][0]["name"] = "other"
        self.relock()
        self.assertFalse(self.check()["plan_valid"])

    def test_only_explicit_nodes_accepted(self):
        for node in ("tests", "tests/test_sample.py", "-k anything", "../escape.py::test_x",
                     "tests/test_sample.py::test_*", "tests/test_sample.py::test_x[param]",
                     "tests/test_sample.py::test_x;whoami", "tests/test_sample.py::helper"):
            self.plan["test_nodes"] = [node]
            self.assertFalse(self.check()["plan_valid"])

    def test_missing_or_duplicate_selected_tests(self):
        for nodes in (["tests/test_missing.py::test_x"], self.plan["test_nodes"] * 2):
            self.plan["test_nodes"] = nodes
            self.assertFalse(self.check()["plan_valid"])

    def test_bounds(self):
        original = copy.deepcopy(self.plan)
        for field, value in (("test_nodes", []), ("test_nodes", ["a"] * 21),
                             ("dependencies", []), ("dependencies", [{}] * 101)):
            self.plan = copy.deepcopy(original)
            self.plan[field] = value
            self.assertFalse(self.check()["plan_valid"])

    def test_semantic_order_does_not_change_plan_identity(self):
        self.plan["dependencies"].append({"name": "synthetic-helper", "version": "0.0.0", "wheel_sha256": "d" * 64})
        self.plan["test_nodes"].append("tests/test_sample.py::test_other")
        self.relock()
        first = self.check()
        self.plan["dependencies"].reverse()
        self.plan["test_nodes"].reverse()
        self.assertEqual(first["plan_sha256"], self.check()["plan_sha256"])

    def test_no_source_import_collection_network_or_install(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No process")), patch("socket.socket.connect", side_effect=AssertionError("No network")), patch.object(Path, "open", side_effect=AssertionError("No filesystem reads during preflight")):
            self.assertTrue(self.check()["plan_valid"])

    def test_invalid_data_is_not_echoed(self):
        marker = "SYNTHETIC_PRIVATE_VALUE"
        self.plan["command"] = marker
        self.assertNotIn(marker, str(self.check()))


if __name__ == "__main__":
    unittest.main()
