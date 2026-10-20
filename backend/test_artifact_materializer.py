from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from artifact_materializer import materialize_source_artifact
from source_artifact import build_source_artifact


class MaterializerTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        self.parent = Path(self.owner.name)
        self.source = self.parent / "source"
        self.source.mkdir()
        (self.source / "src").mkdir()
        self.raw = b"raise RuntimeError('NEVER EXECUTE')\r\n"
        (self.source / "src/a.py").write_bytes(self.raw)
        (self.source / "b.py").write_text("value = 2\n")
        self.artifact = build_source_artifact(self.source, ["src/a.py", "b.py"])

    def materialize(self):
        return materialize_source_artifact(self.artifact.payload, self.artifact.sha256, self.parent)

    def assert_no_staging(self):
        self.assertEqual(list(self.parent.glob("codepilot-input-*")), [])

    def test_exact_files_and_normal_cleanup(self):
        with self.materialize() as (root, manifest):
            self.assertEqual((root / "src/a.py").read_bytes(), self.raw)
            self.assertEqual(manifest, self.artifact.manifest())
            self.assertEqual(sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py")), ["b.py", "src/a.py"])
            self.assertNotEqual(root, self.source)
        self.assertFalse(root.exists())
        self.assertEqual((self.source / "src/a.py").read_bytes(), self.raw)

    def test_verify_failure_precedes_directory_creation(self):
        with patch("artifact_materializer.tempfile.TemporaryDirectory") as create:
            with self.assertRaises(ValueError):
                with materialize_source_artifact(self.artifact.payload + b"x", self.artifact.sha256, self.parent):
                    self.fail("Invalid artifact was published")
        create.assert_not_called()

    def test_missing_parent_not_created(self):
        missing = self.parent / "missing"
        with self.assertRaises(OSError):
            with materialize_source_artifact(self.artifact.payload, self.artifact.sha256, missing):
                pass
        self.assertFalse(missing.exists())

    def test_traversal_rejected_even_with_matching_package_digest(self):
        document = json.loads(self.artifact.payload)
        document["files"][0]["path"] = "../escape.py"
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        with patch("artifact_materializer.tempfile.TemporaryDirectory") as create, self.assertRaises(ValueError):
            with materialize_source_artifact(payload, hashlib.sha256(payload).hexdigest(), self.parent):
                self.fail("Unsafe input published")
        create.assert_not_called()

    def test_parent_reparse_point_rejected(self):
        from artifact_materializer import _ordinary_directory
        import stat
        directory = Mock()
        directory.lstat.return_value = Mock(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with self.assertRaises(ValueError):
            _ordinary_directory(directory)

    def test_late_write_failure_never_publishes_partial_directory(self):
        original = Path.open
        writes = []

        def fail_second(path, mode="r", *args, **kwargs):
            if mode == "xb":
                writes.append(path)
                if len(writes) == 2:
                    raise OSError("synthetic disk failure")
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, "open", new=fail_second), self.assertRaises(OSError):
            with self.materialize():
                self.fail("Partial directory published")
        self.assert_no_staging()
        self.assertEqual(len(writes), 2)

    def test_readback_corruption_rejected(self):
        with patch.object(Path, "read_bytes", return_value=b"corrupt"), self.assertRaises(ValueError):
            with self.materialize():
                self.fail("Corrupted file published")
        self.assert_no_staging()

    def test_consumer_error_or_interruption_cleans_up(self):
        for exception in (RuntimeError, KeyboardInterrupt):
            with self.subTest(exception=exception), self.assertRaises(exception):
                with self.materialize() as (root, _):
                    raise exception()
            self.assertFalse(root.exists())
        self.assert_no_staging()

    def test_existing_sibling_not_changed(self):
        sibling = self.parent / "existing"
        sibling.mkdir()
        (sibling / "b.py").write_text("unchanged")
        with self.materialize() as (root, _):
            self.assertNotEqual(root, sibling)
        self.assertEqual((sibling / "b.py").read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
