import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from check_auth_compatibility_spec import verify_specification


class AuthSpecificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).resolve().parent
        cls.spec = json.loads((directory / "auth_compatibility_spec.json").read_text(encoding="utf-8"))
        cls.source = json.loads((directory / "requests_auth_review_evidence.json").read_text(encoding="utf-8"))
        cls.dependency = json.loads((directory / "requests_dependency_review_evidence.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.report = verify_specification(self.spec)
        self.cases = {row["id"]: row for row in self.report["cases"]}

    def test_all_twelve_specification_cases_consistent(self):
        self.assertEqual(self.report["status"], "specification_consistent")
        self.assertEqual(self.report["case_count"], 12)

    def test_exact_revision_and_git_blob_evidence(self):
        self.assertEqual(self.spec["revision"], self.source["revision"])
        self.assertRegex(self.spec["revision"], r"^[0-9a-f]{40}$")
        self.assertTrue(all(item["git_blob_matches"] for item in self.source["files"]))
        auth = next(item for item in self.source["files"] if item["path"] == "src/requests/auth.py")
        self.assertEqual(auth["literal_encodings_in_basic_auth"], ["latin1", "latin1"])

    def test_ascii_and_opaque_bytes_preserved(self):
        for name in ("ascii", "opaque_bytes", "empty", "colon_in_password"):
            with self.subTest(name=name):
                row = self.cases[name]
                self.assertEqual(row["legacy_reference"], row["per_field_fallback_reference"])
                self.assertFalse(row["unconditional_utf8_changes_existing_header"])

    def test_latin1_default_changes_under_unconditional_utf8(self):
        row = self.cases["latin1_pair"]
        self.assertTrue(row["unconditional_utf8_changes_existing_header"])
        self.assertEqual(row["legacy_reference"]["userpass_hex"], "4af672673a70e47373")
        self.assertEqual(row["legacy_reference"], row["per_field_fallback_reference"])

    def test_fallback_mixes_encodings_for_nonlatin_username(self):
        row = self.cases["nonlatin_username_latin1_password"]
        self.assertTrue(row["fallback_differs_from_uniform_utf8_for_string_pair"])
        self.assertEqual(row["per_field_fallback_reference"]["userpass_hex"], "e794a8e688b73a70e47373")
        self.assertEqual(row["unconditional_utf8_reference"]["userpass_hex"], "e794a8e688b73a70c3a47373")

    def test_fallback_mixes_encodings_for_nonlatin_password(self):
        row = self.cases["latin1_username_nonlatin_password"]
        self.assertTrue(row["fallback_differs_from_uniform_utf8_for_string_pair"])
        self.assertEqual(row["per_field_fallback_reference"]["userpass_hex"], "4af672673ae5af86e7a081")

    def test_nonlatin_behavior_is_a_policy_change_not_proven_fix(self):
        for name in ("nonlatin_username_ascii_password", "both_nonlatin", "decomposed_unicode"):
            self.assertTrue(self.cases[name]["fallback_changes_exception_to_header"])
        self.assertFalse(self.report["patch_approved"])
        self.assertFalse(self.report["repository_tests_executed"])
        self.assertFalse(self.report["candidate_patch_executed"])

    def test_wrong_expected_byte_is_detected(self):
        spec = copy.deepcopy(self.spec)
        spec["cases"][0]["legacy_expected_by_static_inference"]["userpass_hex"] = "00"
        self.assertEqual(verify_specification(spec)["status"], "specification_error")

    def test_dependency_metadata_does_not_claim_environment_is_ready(self):
        self.assertTrue(self.dependency["wheel"]["matches_pypi_digest"])
        self.assertFalse(self.dependency["package_installed"])
        self.assertFalse(self.dependency["package_code_executed"])
        self.assertFalse(self.dependency["transitive_dependencies_resolved"])
        self.assertFalse(self.dependency["dependency_image_verified"])

    def test_oracle_performs_no_io_or_external_execution(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No process")), patch("socket.socket.connect", side_effect=AssertionError("No network")), patch.object(Path, "open", side_effect=AssertionError("No file access")):
            self.assertEqual(verify_specification(self.spec)["status"], "specification_consistent")


if __name__ == "__main__":
    unittest.main()
