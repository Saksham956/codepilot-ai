import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from import_provenance_policy import check_import_receipt
from source_artifact import build_source_artifact
from verify_requests_dependency_lock import verify_lock


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        root = Path(self.owner.name)
        (root / "src/requests").mkdir(parents=True)
        (root / "src/requests/__init__.py").write_text("raise RuntimeError('DO NOT IMPORT')\n")
        (root / "src/requests/auth.py").write_text("raise RuntimeError('DO NOT IMPORT AUTH')\n")
        self.artifact = build_source_artifact(root, ["src/requests/__init__.py", "src/requests/auth.py"])
        self.worker_root = "/tmp/codepilot-input-synthetic"
        self.receipt = {"artifact_sha256": self.artifact.sha256,
                        "package_paths": [self.worker_root + "/src/requests"],
                        "modules": [{"name": "requests" if item["path"].endswith("__init__.py") else "requests.auth",
                                     "origin": self.worker_root + "/" + item["path"], "sha256": item["sha256"]}
                                    for item in self.artifact.manifest()["files"]]}

    def check(self):
        result = check_import_receipt(self.receipt, self.artifact.payload, self.artifact.sha256, self.worker_root)
        self.assertFalse(result["runtime_imports_verified"])
        self.assertFalse(result["checker_imported_repository_code"])
        self.assertFalse(result["repository_execution_enabled"])
        return result

    def test_matching_receipt_is_consistency_only(self):
        self.assertTrue(self.check()["target_modules_consistent"])

    def test_installed_package_rejected(self):
        self.receipt["modules"][0]["origin"] = "/usr/local/lib/python3.13/site-packages/requests/__init__.py"
        self.assertFalse(self.check()["target_modules_consistent"])

    def test_different_job_root_rejected(self):
        self.receipt["modules"][0]["origin"] = self.receipt["modules"][0]["origin"].replace("synthetic", "other")
        self.assertFalse(self.check()["target_modules_consistent"])

    def test_changed_bytes_rejected(self):
        self.receipt["modules"][1]["sha256"] = "0" * 64
        self.assertFalse(self.check()["target_modules_consistent"])

    def test_extra_package_search_path_rejected(self):
        self.receipt["package_paths"].append("/usr/local/lib/site-packages/requests")
        self.assertFalse(self.check()["target_modules_consistent"])

    def test_artifact_identity_rejected(self):
        self.receipt["artifact_sha256"] = "0" * 64
        self.assertFalse(self.check()["target_modules_consistent"])

    def test_missing_duplicate_or_unknown_module_rejected(self):
        original = copy.deepcopy(self.receipt)
        for modules in (original["modules"][:1], [original["modules"][0]] * 2,
                        [{**original["modules"][0], "name": "requests.other"}, original["modules"][1]]):
            self.receipt["modules"] = modules
            self.assertFalse(self.check()["target_modules_consistent"])

    def test_noncanonical_and_traversal_origin_rejected(self):
        for origin in (self.worker_root + "/src/../src/requests/__init__.py", self.worker_root + "//src/requests/__init__.py"):
            self.receipt["modules"][0]["origin"] = origin
            self.assertFalse(self.check()["target_modules_consistent"])

    def test_data_checker_does_not_read_source_or_execute(self):
        with patch.object(Path, "open", side_effect=AssertionError("No source reads")), patch("subprocess.Popen", side_effect=AssertionError("No process")):
            self.assertTrue(self.check()["target_modules_consistent"])


class LockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = Path(__file__).resolve().parent / "requests_harness_inputs"

    def test_actual_wheels_and_target_metadata_closure(self):
        result = verify_lock(self.inputs)
        self.assertTrue(result["metadata_closure_verified"])
        self.assertEqual(result["package_count"], 9)
        self.assertFalse(result["runtime_image_verified"])
        self.assertFalse(result["packages_installed"])

    def changed_copy(self):
        owner = tempfile.TemporaryDirectory()
        self.addCleanup(owner.cleanup)
        root = Path(owner.name) / "inputs"
        shutil.copytree(self.inputs, root)
        return root

    def test_wheel_tampering_detected(self):
        root = self.changed_copy()
        wheel = next((root / "wheels").iterdir())
        wheel.write_bytes(wheel.read_bytes() + b"tampered")
        self.assertFalse(verify_lock(root)["metadata_closure_verified"])

    def test_declared_version_tampering_detected(self):
        root = self.changed_copy()
        path = root / "requests_harness_dependency_lock.json"
        data = json.loads(path.read_text())
        data["dependencies"][0]["version"] = "0.0.0"
        path.write_text(json.dumps(data))
        self.assertFalse(verify_lock(root)["metadata_closure_verified"])

    def test_missing_transitive_dependency_detected(self):
        root = self.changed_copy()
        path = root / "requests_harness_dependency_lock.json"
        data = json.loads(path.read_text())
        data["dependencies"] = [row for row in data["dependencies"] if row["name"] != "pluggy"]
        path.write_text(json.dumps(data))
        self.assertFalse(verify_lock(root)["metadata_closure_verified"])

    def test_text_lock_drift_detected(self):
        root = self.changed_copy()
        (root / "requests_harness_requirements.lock").write_text("pytest>=8\n")
        self.assertFalse(verify_lock(root)["metadata_closure_verified"])

    def test_offline_verification_does_not_use_network_or_processes(self):
        with patch("socket.socket.connect", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No process")):
            self.assertTrue(verify_lock(self.inputs)["metadata_closure_verified"])


if __name__ == "__main__":
    unittest.main()
