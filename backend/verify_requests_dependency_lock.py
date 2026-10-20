"""Offline metadata verification of the candidate wheel set; never install it."""
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import zipfile

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from patch_validator import safe_file
from prepare_requests_dependency_lock import ROOTS, TARGET, active_requirements
from repository_test_policy import dependency_lock_digest


def verify_lock(root):
    report = {"status": "lock_invalid", "metadata_closure_verified": False,
              "packages_installed": False, "runtime_image_verified": False,
              "execution_approved": False, "repository_execution_enabled": False}
    try:
        root = Path(root)
        lock = json.loads(safe_file(root, "requests_harness_dependency_lock.json").read_text(encoding="utf-8"))
        if lock["schema_version"] != 1 or lock["root_requirements"] != ROOTS or lock["target"] != TARGET:
            raise ValueError("Lock target or root selection changed")
        rows = lock["dependencies"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
            raise ValueError("Invalid lock dependency count")
        by_name = {}
        parsed_requires = {}
        total = 0
        for row in rows:
            name = row["name"]
            if canonicalize_name(name) != name or name in by_name or name == "requests":
                raise ValueError("Invalid or duplicate dependency; Requests must come from source")
            path = safe_file(root / "wheels", row["filename"])
            if path.stat().st_size > 5_000_000:
                raise ValueError("Wheel exceeds input size limit")
            raw = path.read_bytes()
            total += len(raw)
            if total > 32 * 1024 * 1024 or len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["wheel_sha256"]:
                raise ValueError("Wheel bytes differ from lock")
            distribution, wheel_version, _, tags = parse_wheel_filename(row["filename"])
            if distribution != name or wheel_version != Version(row["version"]) or not any(
                tag.interpreter == "py3" and tag.abi == "none" and tag.platform == "any" for tag in tags
            ):
                raise ValueError("Wheel tags do not match the reviewed target policy")
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = [item.filename for item in archive.infolist()]
                if len(names) != len(set(names)) or sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                    raise ValueError("Wheel layout exceeds policy")
                for name_in_zip in names:
                    if (name_in_zip.startswith(("/", "\\")) or ".." in name_in_zip.replace("\\", "/").split("/")
                            or ":" in name_in_zip or name_in_zip.endswith(".pth") or ".data/scripts/" in name_in_zip):
                        raise ValueError("Wheel contains blocked paths or startup files")
                entries = [item for item in archive.infolist() if item.filename.endswith(".dist-info/METADATA")]
                if len(entries) != 1 or entries[0].file_size > 256_000:
                    raise ValueError("Invalid metadata entry")
                metadata = BytesParser().parsebytes(archive.read(entries[0]))
            if canonicalize_name(metadata["Name"]) != name or Version(metadata["Version"]) != Version(row["version"]):
                raise ValueError("Wheel identity differs from lock")
            if metadata["Requires-Python"] and Version(TARGET["python_full_version"]) not in SpecifierSet(metadata["Requires-Python"]):
                raise ValueError("Wheel does not support the declared Python version")
            requirements = metadata.get_all("Requires-Dist", [])
            if requirements != row["requires_dist"]:
                raise ValueError("Dependency metadata changed")
            parsed_requires[name] = active_requirements(requirements)
            by_name[name] = row
        reached = set()
        pending = active_requirements(ROOTS)
        while pending:
            requirement = pending.pop()
            name = canonicalize_name(requirement.name)
            if name not in by_name or Version(by_name[name]["version"]) not in requirement.specifier:
                raise ValueError("Dependency constraint is not satisfied")
            if name not in reached:
                reached.add(name)
                pending.extend(parsed_requires[name])
        if reached != set(by_name):
            raise ValueError("Lock includes unneeded dependencies")
        pins = [{key: row[key] for key in ("name", "version", "wheel_sha256")} for row in rows]
        if dependency_lock_digest(pins) != lock["dependency_lock_sha256"]:
            raise ValueError("Lock identity differs from declared pins")
        expected_text = ["# Candidate review lock for Linux CPython 3.13; not installation approval.",
                         "# Requests itself is excluded: the library under test must come from the selected source snapshot."]
        expected_text += [f"{row['name']}=={row['version']} --hash=sha256:{row['wheel_sha256']}" for row in rows]
        if safe_file(root, "requests_harness_requirements.lock").read_text(encoding="utf-8") != "\n".join(expected_text) + "\n":
            raise ValueError("Text lock and JSON lock differ")
        report.update(status="candidate_lock_verified_offline", metadata_closure_verified=True,
                      package_count=len(rows), total_wheel_bytes=total,
                      dependency_lock_sha256=lock["dependency_lock_sha256"])
    except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile):
        pass
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify_lock(args.inputs)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["metadata_closure_verified"] else 1)
