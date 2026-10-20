"""Bounded source-only artifacts. Never import or execute selected files."""
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from patch_validator import safe_file


MAX_FILES = 100
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 6 * 1024 * 1024
BLOCKED_PARTS = {
    "secrets", "secret", "credentials", "credential", "private", "keys",
    "certs", "venv", "node_modules", "__pycache__", "build", "dist",
}
BLOCKED_FILES = {"setup.py", "conftest.py", "sitecustomize.py", "usercustomize.py"}
SECRET_MARKERS = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|AKIA[A-Z0-9]{16}|"
    r"(?:api[_-]?key|access[_-]?token|password|secret)\s*=\s*['\"][^'\"\r\n]+['\"]",
    re.IGNORECASE,
)


def approved_path(value):
    if not isinstance(value, str) or len(value) > 240:
        raise ValueError("Expected a bounded source path")
    value = value.replace("\\", "/")
    parts = value.split("/")
    if not parts or any(
        not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", part)
        or part.endswith(".") or part.casefold() in BLOCKED_PARTS
        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise ValueError("Unsafe or sensitive source path")
    name = parts[-1].casefold()
    if not name.endswith(".py") or name in BLOCKED_FILES:
        raise ValueError("Only ordinary Python source files are approved")
    if any(word in name for word in ("secret", "credential", "private_key")):
        raise ValueError("Sensitive source filename requires manual review")
    return "/".join(parts)


def _source_bytes(raw):
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Source file exceeds size limit")
    text = raw.decode("utf-8")
    if "\0" in text or SECRET_MARKERS.search(text):
        raise ValueError("Source contains a blocked content marker; manual review required")


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _reject_prefix_collisions(names):
    folded = {name.casefold() for name in names}
    for name in names:
        parts = name.casefold().split("/")
        if any("/".join(parts[:index]) in folded for index in range(1, len(parts))):
            raise ValueError("A file cannot also be an artifact directory")


@dataclass(frozen=True)
class SourceArtifact:
    payload: bytes
    sha256: str
    # JSON string keeps the immutable artifact's manifest from being mutated.
    manifest_json: str

    def manifest(self):
        return json.loads(self.manifest_json)


def build_source_artifact(repo_path, selected_files):
    """Trusted backend selection only; fail the entire batch on any rejection.

    Assumes private source snapshots with no concurrent external writers.
    Nothing is written to disk. No recursive discovery or archive extraction.
    """
    if not isinstance(selected_files, (list, tuple)) or not 1 <= len(selected_files) <= MAX_FILES:
        raise ValueError("Select between 1 and 100 explicit files")
    names = [approved_path(name) for name in selected_files]
    aliases = [unicodedata.normalize("NFC", name).casefold() for name in names]
    if len(set(aliases)) != len(aliases):
        raise ValueError("Duplicate or case-alias source paths")
    _reject_prefix_collisions(names)
    files = []
    total = 0
    for name in sorted(names):
        path = safe_file(repo_path, name)
        before = path.stat()
        if before.st_nlink != 1:
            raise ValueError("Hard-linked files are not approved")
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError("Source file exceeds size limit")
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        after = safe_file(repo_path, name).stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("Source changed during packaging")
        _source_bytes(raw)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Selected source exceeds total size limit")
        files.append({"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                      "content_b64": base64.b64encode(raw).decode("ascii")})
    payload = _json_bytes({"version": 1, "files": files})
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("Encoded artifact exceeds size limit")
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {"version": 1, "artifact_sha256": digest, "total_bytes": total,
                "files": [{k: value for k, value in file.items() if k != "content_b64"} for file in files]}
    return SourceArtifact(payload, digest, _json_bytes(manifest).decode("ascii"))


def verify_source_artifact(payload, expected_sha256):
    """Verify a received artifact; return manifest only, never extract or run it."""
    if not isinstance(payload, bytes) or len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("Invalid artifact size or type")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected an independently supplied artifact digest")
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("Artifact digest mismatch")
    document = json.loads(payload)
    if not isinstance(document, dict) or set(document) != {"version", "files"} or type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("Unknown artifact format")
    files = document["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Invalid artifact file count")
    seen = set()
    total = 0
    entries = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256", "content_b64"}:
            raise ValueError("Invalid artifact entry")
        name = approved_path(item["path"])
        if name != item["path"] or name.casefold() in seen:
            raise ValueError("Noncanonical or duplicate source path")
        seen.add(name.casefold())
        if type(item["size"]) is not int or not 0 <= item["size"] <= MAX_FILE_BYTES:
            raise ValueError("Invalid file size")
        if not isinstance(item["content_b64"], str) or len(item["content_b64"]) > (MAX_FILE_BYTES + 2) // 3 * 4:
            raise ValueError("Invalid encoded content size")
        raw = base64.b64decode(item["content_b64"], validate=True)
        _source_bytes(raw)
        if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("File size or digest mismatch")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Artifact exceeds total size limit")
        entries.append({"path": name, "size": len(raw), "sha256": item["sha256"]})
    if payload != _json_bytes(document):
        raise ValueError("Artifact must use canonical JSON")
    if [entry["path"] for entry in entries] != sorted(entry["path"] for entry in entries):
        raise ValueError("Artifact entries must be sorted")
    _reject_prefix_collisions([entry["path"] for entry in entries])
    return {"version": 1, "artifact_sha256": expected_sha256, "total_bytes": total, "files": entries}
