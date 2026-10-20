"""Static validation only: repository source is never imported or executed."""
import ast
import difflib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile


def normalize_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("File path must be a nonempty string")
    value = value.replace("\\", "/")
    parts = value.split("/")
    if any(
        part in ("", ".", "..") or part.lower() == ".git"
        or part.endswith((" ", "."))
        or re.search(r'[<>:"|?*\x00-\x1f]', part)
        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise ValueError("Unsafe file path")
    return "/".join(parts)


def _reject_link(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise ValueError("Symlinks and Windows reparse points are not allowed")


def safe_file(root, filename):
    root = Path(root).absolute()
    _reject_link(root)
    path = root
    for part in normalize_path(filename).split("/"):
        path = path / part
        _reject_link(path)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Proposed file must be an ordinary repository file")
    return path


def assert_safe_tree(root):
    """Reject links before analysis or copying; never descend into them."""
    root = Path(root).absolute()
    _reject_link(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        if Path(directory) == root:
            dirs[:] = [name for name in dirs if name != ".git"]
        for name in dirs + files:
            path = Path(directory) / name
            _reject_link(path)
            if not (path.is_file() or path.is_dir()):
                raise ValueError("Special repository files are not allowed")


def validate_file_changes(source, changes, filename):
    """Locate every edit in the original, reject overlaps, parse the final file."""
    try:
        ast.parse(source)
        spans = []
        for change in changes:
            original = change.get("original_code")
            replacement = change.get("replacement_code")
            if not isinstance(original, str) or not original:
                raise ValueError("original_code must be a nonempty string")
            if not isinstance(replacement, str):
                raise ValueError("replacement_code must be a string")
            start = source.find(original)
            if start < 0 or source.find(original, start + 1) >= 0:
                raise ValueError("Original code must match exactly once in the full file")
            spans.append((start, start + len(original), replacement))
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError("Changes overlap or duplicate the same original code")
        modified = source
        for start, end, replacement in reversed(spans):
            modified = modified[:start] + replacement + modified[end:]
        ast.parse(modified)
        return {
            "file": filename, "valid": True, "syntax_valid": True,
            "modified_source": modified,
            "diff": "".join(difflib.unified_diff(
                source.splitlines(keepends=True), modified.splitlines(keepends=True),
                fromfile="a/" + filename, tofile="b/" + filename,
            )),
        }
    except (SyntaxError, ValueError, TypeError) as error:
        return {"file": filename, "valid": False, "error": str(error)}


def validate_patch(original_source, original_code, replacement_code):
    """Compatibility wrapper for the earlier single-change lesson."""
    return validate_file_changes(original_source, [{
        "original_code": original_code, "replacement_code": replacement_code,
    }], "source.py")


def prepare_patch_copy(repo_path, changes, allowed_files):
    """Return (temporary-directory owner or None, JSON-safe report).

    No writes occur until the entire batch validates. A failed copy or write
    destroys the unpublished staging directory. The caller owns cleanup.
    Assumes a private clone with no concurrent external writers.
    """
    assert_safe_tree(repo_path)
    if not isinstance(changes, list) or len(changes) > 100:
        raise ValueError("changes must be a list with at most 100 entries")
    allowed = {normalize_path(name) for name in allowed_files}
    grouped = {}
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Each change must be an object")
        filename = normalize_path(change.get("file"))
        if filename not in allowed or not filename.endswith(".py"):
            raise ValueError("AI proposed a change to an unknown Python file")
        grouped.setdefault(filename, []).append(change)

    results = []
    staged = {}
    for filename, edits in grouped.items():
        path = safe_file(repo_path, filename)
        if path.stat().st_size > 1_000_000:
            raise ValueError("Proposed file is too large")
        raw = path.read_bytes()
        source = raw.decode("utf-8")
        if b"\r\n" in raw and b"\n" in raw.replace(b"\r\n", b""):
            raise ValueError("Mixed newline styles require manual review")
        # Match AI snippets with normalized newlines; preserve the file's style.
        newline = "\r\n" if b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"") else "\n"
        source = source.replace("\r\n", "\n")
        edits = [{**edit, "original_code": edit.get("original_code", "").replace("\r\n", "\n"),
                  "replacement_code": edit.get("replacement_code", "").replace("\r\n", "\n")}
                 if isinstance(edit.get("original_code"), str) and isinstance(edit.get("replacement_code"), str)
                 else edit for edit in edits]
        result = validate_file_changes(source, edits, filename)
        modified = result.pop("modified_source", None)
        if result["valid"]:
            staged[filename] = modified.replace("\n", newline).encode("utf-8")
        results.append(result)
    report = {"validation": results, "all_valid": bool(results) and all(r["valid"] for r in results),
              "applied": False, "tested": False, "application_scope": "temporary_working_copy"}
    if not report["all_valid"]:
        return None, report
    owner = tempfile.TemporaryDirectory(prefix="codepilot-patch-")
    try:
        target = Path(owner.name) / "repository"
        shutil.copytree(repo_path, target, ignore=shutil.ignore_patterns(".git"))
        assert_safe_tree(target)
        for filename, contents in staged.items():
            safe_file(target, filename).write_bytes(contents)
        report["applied"] = True
        return owner, report
    except Exception:
        owner.cleanup()
        raise
