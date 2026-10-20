import base64
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from source_artifact import build_source_artifact, verify_source_artifact, approved_path, _json_bytes


class SourceArtifactTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        self.root = Path(self.owner.name) / "repo"
        self.root.mkdir()
        self.raw = b"raise RuntimeError('NOT EXECUTED')\r\nvalue = 1\r\n"
        (self.root / "sample.py").write_bytes(self.raw)
        (self.root / ".env").write_text("SYNTHETIC_DO_NOT_INCLUDE")
        (self.root / "unselected.py").write_text("value = 2\n")

    def build(self, names=None):
        return build_source_artifact(self.root, names if names is not None else ["sample.py"])

    def test_only_selected_files_manifest_and_roundtrip(self):
        artifact = self.build()
        self.assertEqual(verify_source_artifact(artifact.payload, artifact.sha256), artifact.manifest())
        document = json.loads(artifact.payload)
        self.assertEqual([item["path"] for item in document["files"]], ["sample.py"])
        self.assertEqual(base64.b64decode(document["files"][0]["content_b64"]), self.raw)
        self.assertEqual(artifact.manifest()["total_bytes"], len(self.raw))
        self.assertNotIn("content_b64", str(artifact.manifest()))
        self.assertNotIn(str(self.root), artifact.payload.decode())
        self.assertEqual((self.root / "sample.py").read_bytes(), self.raw)

    def test_deterministic_order_and_windows_separators(self):
        (self.root / "src").mkdir()
        (self.root / "src/a.py").write_text("x = 1\n")
        a = self.build(["src\\a.py", "sample.py"])
        b = self.build(["sample.py", "src/a.py"])
        self.assertEqual(a, b)

    def test_manifest_is_immutable_from_callers(self):
        artifact = self.build()
        manifest = artifact.manifest()
        manifest["files"].clear()
        self.assertEqual(len(artifact.manifest()["files"]), 1)

    def test_empty_and_excessive_selection(self):
        for selected in [[], ["sample.py"] * 101, "sample.py"]:
            with self.subTest(selected=str(selected)[:60]), self.assertRaises(ValueError):
                self.build(selected)

    def test_paths_and_sensitive_names_rejected_before_reads(self):
        for name in ["../x.py", "C:/x.py", "/x.py", "a//b.py", "a/../b.py", "CON.py",
                     ".env", ".git/config.py", "src/.aws/key.py", "secrets/x.py", "credentials.py",
                     "private/x.py", "setup.py", "conftest.py", "sitecustomize.py", "usercustomize.py",
                     "x.py:stream", "a./x.py", "a\x00.py", "a/../../x.py"]:
            with self.subTest(name=name), patch.object(Path, "open", side_effect=AssertionError("Must reject before reading")), self.assertRaises(ValueError):
                self.build(["sample.py", name])

    def test_aliases_and_prefix_collisions(self):
        for names in [["sample.py", "sample.py"], ["sample.py", "SAMPLE.py"], ["sample.py", "sample.py/x.py"]]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.build(names)

    def test_file_size_limit(self):
        with patch("source_artifact.MAX_FILE_BYTES", 8), self.assertRaises(ValueError):
            self.build()

    def test_total_and_encoded_size_limits(self):
        with patch("source_artifact.MAX_TOTAL_BYTES", len(self.raw)), self.assertRaises(ValueError):
            self.build(["sample.py", "unselected.py"])
        with patch("source_artifact.MAX_ARTIFACT_BYTES", 16), self.assertRaises(ValueError):
            self.build()

    def test_content_markers_and_binary_rejected(self):
        examples = [b"password = 'synthetic-only'", b"api_key='synthetic-key'",
                    b"# -----BEGIN RSA PRIVATE KEY-----", b"# AKIAABCDEFGHIJKLMNOP", b"\x00", b"\xff"]
        for raw in examples:
            with self.subTest(raw=raw):
                (self.root / "sample.py").write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.build()

    def test_missing_files_and_directories(self):
        with self.assertRaises(OSError):
            self.build(["missing.py"])
        (self.root / "dir.py").mkdir()
        with self.assertRaises(ValueError):
            self.build(["dir.py"])

    def test_reparse_points_and_links_rejected(self):
        original = Path.lstat
        target = self.root / "sample.py"

        def fake_lstat(path, *args, **kwargs):
            if path == target:
                return Mock(st_mode=stat.S_IFREG, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", new=fake_lstat), self.assertRaises(ValueError):
            self.build()

    def test_hardlinks_rejected(self):
        path = Mock()
        path.stat.return_value = Mock(st_nlink=2)
        with patch("source_artifact.safe_file", return_value=path), self.assertRaises(ValueError):
            self.build()
        path.open.assert_not_called()

    def test_tampering_and_wrong_digest(self):
        artifact = self.build()
        for payload, digest in [(artifact.payload + b" ", artifact.sha256), (artifact.payload, "0" * 64), (artifact.payload, "bad")]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                verify_source_artifact(payload, digest)

    def test_received_entries_checked_independently(self):
        artifact = self.build()
        for field, value in [("path", "../escape.py"), ("path", ".env"), ("size", True),
                             ("size", 10000000), ("sha256", "0" * 64), ("content_b64", "!!!!")]:
            doc = json.loads(artifact.payload)
            doc["files"][0][field] = value
            payload = _json_bytes(doc)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                verify_source_artifact(payload, hashlib.sha256(payload).hexdigest())

    def test_received_format_and_canonical_encoding(self):
        artifact = self.build()
        documents = [{"version": True, "files": []}, {"version": 2, "files": []},
                     {"version": 1, "files": []}, {"version": 1, "files": [], "extra": 1}]
        for document in documents:
            payload = _json_bytes(document)
            with self.assertRaises(ValueError):
                verify_source_artifact(payload, hashlib.sha256(payload).hexdigest())
        payload = json.dumps(json.loads(artifact.payload), indent=2).encode()
        with self.assertRaises(ValueError):
            verify_source_artifact(payload, hashlib.sha256(payload).hexdigest())

    def test_no_partial_output_on_late_content_rejection(self):
        (self.root / "unselected.py").write_text("secret='synthetic-only'\n")
        before = sorted(path.name for path in self.root.iterdir())
        with self.assertRaises(ValueError):
            self.build(["sample.py", "unselected.py"])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
