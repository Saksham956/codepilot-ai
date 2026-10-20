"""Trusted synthetic fixtures only; no cloned repository tests are executed."""
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch, Mock

from patch_validator import normalize_path, prepare_patch_copy, validate_file_changes


def edit(file="sample.py", original="value = 1", replacement="value = 2"):
    return {"file": file, "original_code": original, "replacement_code": replacement}


class SafePatchingTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        self.repo = Path(self.owner.name) / "repo"
        self.repo.mkdir()
        (self.repo / "sample.py").write_bytes(b"value = 1\r\nother = 3\r\n")

    def prepare(self, changes):
        return prepare_patch_copy(self.repo, changes, ["sample.py"])

    def test_combined_edits_and_original_unchanged(self):
        owner, report = self.prepare([edit(), edit(original="other = 3", replacement="other = 4")])
        self.addCleanup(owner.cleanup)
        self.assertTrue(report["applied"])
        self.assertFalse(report["tested"])
        self.assertEqual((Path(owner.name) / "repository/sample.py").read_bytes(), b"value = 2\r\nother = 4\r\n")
        self.assertEqual((self.repo / "sample.py").read_bytes(), b"value = 1\r\nother = 3\r\n")

    def test_final_syntax_checked_together(self):
        result = validate_file_changes("x = (1)\n", [edit(original="(", replacement="["), edit(original=")", replacement="]")], "sample.py")
        self.assertTrue(result["valid"])

    def test_no_staging_on_any_invalid_file(self):
        (self.repo / "bad.py").write_text("x = 1\n")
        with patch("patch_validator.tempfile.TemporaryDirectory") as make_temp:
            owner, report = prepare_patch_copy(self.repo, [edit(), edit("bad.py", "x = 1", "x = (")], ["sample.py", "bad.py"])
        self.assertIsNone(owner)
        self.assertFalse(report["applied"])
        make_temp.assert_not_called()

    def test_ambiguous_duplicate_overlap_and_generated_matches(self):
        cases = [
            ("x = 1\nx = 1\n", [edit(original="x = 1")]),
            ("value = 1\n", [edit(), edit()]),
            ("value = 1\n", [edit(), edit(original="1", replacement="3")]),
            ("value = 1\n", [edit(), edit(original="value = 2", replacement="value = 3")]),
        ]
        for source, changes in cases:
            with self.subTest(source=source, changes=changes):
                self.assertFalse(validate_file_changes(source, changes, "sample.py")["valid"])

    def test_unsafe_paths(self):
        for filename in ["../x.py", "a/../x.py", "/x.py", "C:/x.py", "C:x.py", "\\\\server\\x.py", "x.py:stream", ".git/config", "a//x.py", "a./x.py", "CON.py", "x\x00.py"]:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                normalize_path(filename)

    def test_unknown_and_empty(self):
        with self.assertRaises(ValueError):
            self.prepare([edit("unknown.py")])
        owner, report = self.prepare([])
        self.assertIsNone(owner)
        self.assertFalse(report["all_valid"])

    def test_malformed_edits(self):
        for bad in [None, 42, ""]:
            self.assertFalse(validate_file_changes("value = 1\n", [edit(original=bad)], "sample.py")["valid"])

    def test_copy_write_failure_cleans_unpublished_copy(self):
        staging = tempfile.TemporaryDirectory()
        with patch.object(Path, "write_bytes", side_effect=OSError("simulated write failure")), patch("patch_validator.tempfile.TemporaryDirectory", return_value=staging):
            with self.assertRaises(OSError):
                self.prepare([edit()])
            self.assertFalse(Path(staging.name).exists())
        self.assertIn(b"value = 1", (self.repo / "sample.py").read_bytes())

    def test_windows_reparse_point_is_rejected(self):
        from patch_validator import _reject_link
        path = Mock()
        path.lstat.return_value = Mock(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with self.assertRaises(ValueError):
            _reject_link(path)

    def test_complete_file_beyond_prompt_snippet(self):
        source = "# context\n" * 1000 + "value = 1\n"
        (self.repo / "sample.py").write_text(source, encoding="utf-8")
        owner, report = self.prepare([edit()])
        self.addCleanup(owner.cleanup)
        self.assertTrue(report["all_valid"])
        self.assertIn("value = 2", (Path(owner.name) / "repository/sample.py").read_text())

    def test_metadata_not_copied(self):
        (self.repo / ".git").mkdir()
        owner, _ = self.prepare([edit()])
        self.addCleanup(owner.cleanup)
        self.assertFalse((Path(owner.name) / "repository/.git").exists())

    def test_symlink_file_and_directory(self):
        outside = Path(self.owner.name) / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("secret = 1\n")
        link = self.repo / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable")
        with self.assertRaises(ValueError):
            self.prepare([edit()])


if __name__ == "__main__":
    unittest.main()
