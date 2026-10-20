"""Materialize verified data in a new private directory; never execute it."""
from contextlib import contextmanager
import base64
import hashlib
import json
from pathlib import Path
import stat
import tempfile

from source_artifact import verify_source_artifact


def _ordinary_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("Materialization requires an ordinary private directory")


@contextmanager
def materialize_source_artifact(payload, expected_sha256, scratch_parent):
    """Publish only after all files are written and rehashed; always clean up.

    scratch_parent must be worker-owned and free of concurrent external writers.
    This function creates its destination; it never merges into an existing tree.
    """
    manifest = verify_source_artifact(payload, expected_sha256)
    parent = Path(scratch_parent).absolute()
    _ordinary_directory(parent)
    with tempfile.TemporaryDirectory(dir=parent, prefix="codepilot-input-") as directory:
        root = Path(directory)
        _ordinary_directory(root)
        for entry in json.loads(payload)["files"]:
            path = root
            for component in entry["path"].split("/")[:-1]:
                path = path / component
                path.mkdir(exist_ok=True)
                _ordinary_directory(path)
            target = path / entry["path"].split("/")[-1]
            if not target.resolve().is_relative_to(root.resolve()):
                raise ValueError("Materialization path escaped its directory")
            raw = base64.b64decode(entry["content_b64"], validate=True)
            # Exclusive creation: no overwriting files or following an existing link.
            with target.open("xb") as stream:
                stream.write(raw)
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (
                getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("Materialized file must be ordinary and unlinked")
            if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("Materialized bytes differ from the manifest")
            target.chmod(0o444)
        yield root, manifest
