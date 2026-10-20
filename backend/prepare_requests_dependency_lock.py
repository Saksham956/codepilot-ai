"""Resolve fixed Requests-harness inputs from metadata and hash wheel bytes.

No pip, package installation, source builds, or downloaded code imports occur.
Candidate lock only: it is not a security approval or a prepared worker image.
"""
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import urllib.request
from urllib.parse import urlparse
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from repository_test_policy import dependency_lock_digest


ROOTS = ["charset_normalizer>=2,<4", "idna>=2.5,<4", "urllib3>=1.21.1,<3",
         "certifi>=2017.4.17", "pytest>=6.2,<9"]
TARGET = {"python_version": "3.13", "python_full_version": "3.13.0",
          "implementation_name": "cpython", "implementation_version": "3.13.0",
          "platform_python_implementation": "CPython", "os_name": "posix", "sys_platform": "linux",
          "platform_system": "Linux", "platform_machine": "x86_64", "platform_release": "unknown",
          "platform_version": "unknown", "extra": ""}


def fetch(url, limit=5_000_000):
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in {"pypi.org", "files.pythonhosted.org"}:
        raise ValueError("Unapproved package metadata host")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CodePilot-data-only-lock"}), timeout=30) as response:
        if urlparse(response.geturl()).hostname not in {"pypi.org", "files.pythonhosted.org"}:
            raise ValueError("Unexpected package redirect")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Package metadata or wheel exceeds size limit")
    return raw


def active_requirements(strings):
    result = []
    for text in strings:
        requirement = Requirement(text)
        if requirement.marker and any(name in str(requirement.marker) for name in ("platform_release", "platform_version")):
            raise ValueError("Kernel-specific markers require a separately reviewed target")
        if requirement.marker is not None and not requirement.marker.evaluate(TARGET):
            continue
        if requirement.url or requirement.extras:
            raise ValueError("URL and extra dependency requests need separate review")
        result.append(requirement)
    return result


def universal_wheels(release):
    result = []
    for item in release["urls"]:
        if item["packagetype"] != "bdist_wheel" or item.get("yanked"):
            continue
        if item.get("requires_python") and Version("3.13.0") not in SpecifierSet(item["requires_python"]):
            continue
        _, _, _, tags = parse_wheel_filename(item["filename"])
        if any(tag.interpreter == "py3" and tag.abi == "none" and tag.platform == "any" for tag in tags):
            result.append(item)
    return sorted(result, key=lambda item: item["filename"])


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    wheel_dir = output / "wheels"
    wheel_dir.mkdir(exist_ok=True)
    selected = {}
    cache = {}

    def release(name, version):
        key = (name, str(version))
        if key not in cache:
            cache[key] = json.loads(fetch(f"https://pypi.org/pypi/{name}/{version}/json"))
        return cache[key]

    for _ in range(50):
        constraints = {}
        pending = active_requirements(ROOTS)
        expanded = set()
        while pending:
            requirement = pending.pop()
            name = canonicalize_name(requirement.name)
            constraints.setdefault(name, []).append(requirement)
            if name in selected and name not in expanded:
                expanded.add(name)
                pending.extend(active_requirements(selected[name]["info"].get("requires_dist") or []))
        if len(constraints) > 50:
            raise ValueError("Dependency graph exceeds pilot limit")
        changed = False
        for name, rules in sorted(constraints.items()):
            current = selected.get(name)
            if current and all(Version(current["info"]["version"]) in rule.specifier for rule in rules):
                continue
            index = json.loads(fetch(f"https://pypi.org/pypi/{name}/json"))
            versions = sorted((Version(value) for value in index["releases"]), reverse=True)
            choice = None
            for version in versions:
                if version.is_prerelease or version.is_devrelease or not all(version in rule.specifier for rule in rules):
                    continue
                candidate = release(name, version)
                if universal_wheels(candidate):
                    choice = candidate
                    break
            if choice is None:
                raise ValueError("No compatible universal wheel for a required dependency")
            selected[name] = choice
            changed = True
        if not changed:
            # Remove packages no longer reachable after a reselection, then check again.
            stale = set(selected) - set(constraints)
            for name in stale:
                del selected[name]
            if stale:
                continue
            break
    else:
        raise ValueError("Dependency selection did not stabilize; manual resolution needed")

    rows = []
    total = 0
    for name, metadata in sorted(selected.items()):
        wheel = universal_wheels(metadata)[0]
        raw = fetch(wheel["url"])
        total += len(raw)
        if total > 32 * 1024 * 1024:
            raise ValueError("Wheel input budget exceeded")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != wheel["digests"]["sha256"]:
            raise ValueError("Wheel hash mismatch")
        filename = wheel["filename"]
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("Unsafe wheel filename")
        startup = []
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if sum(item.file_size for item in infos) > 64 * 1024 * 1024:
                raise ValueError("Wheel expanded size exceeds pilot limit")
            metadata_entries = [item for item in infos if item.filename.endswith(".dist-info/METADATA")]
            if len(metadata_entries) != 1 or metadata_entries[0].file_size > 256_000:
                raise ValueError("Unexpected wheel metadata layout")
            for item in infos:
                parts = item.filename.replace("\\", "/").split("/")
                if item.filename.startswith(("/", "\\")) or ".." in parts or ":" in item.filename:
                    raise ValueError("Unsafe wheel entry path")
                if item.filename.endswith(".pth") or ".data/scripts/" in item.filename:
                    startup.append(item.filename)
            parsed = BytesParser().parsebytes(archive.read(metadata_entries[0]))
        if canonicalize_name(parsed["Name"]) != name or Version(parsed["Version"]) != Version(metadata["info"]["version"]):
            raise ValueError("Wheel distribution identity mismatch")
        if parsed["Requires-Python"] and Version("3.13.0") not in SpecifierSet(parsed["Requires-Python"]):
            raise ValueError("Wheel metadata does not support the declared Python target")
        wheel_requirements = parsed.get_all("Requires-Dist", [])
        for requirement in active_requirements(wheel_requirements):
            dependency = selected.get(canonicalize_name(requirement.name))
            if dependency is None or Version(dependency["info"]["version"]) not in requirement.specifier:
                raise ValueError("Wheel metadata dependency closure is incomplete")
        if startup:
            raise ValueError("Wheel startup files require additional review")
        destination = wheel_dir / filename
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError("Existing wheel cache differs")
        destination.write_bytes(raw)
        rows.append({"name": name, "version": str(Version(parsed["Version"])), "wheel_sha256": digest,
                     "filename": filename, "bytes": len(raw), "source_url": wheel["url"],
                     "requires_python": parsed["Requires-Python"], "requires_dist": wheel_requirements,
                     "startup_files_detected": startup, "matches_pypi_digest": True})
    pins = [{key: row[key] for key in ("name", "version", "wheel_sha256")} for row in rows]
    result = {"schema_version": 1, "status": "candidate_lock_metadata_verified", "root_requirements": ROOTS,
              "target": TARGET, "dependencies": rows, "dependency_lock_sha256": dependency_lock_digest(pins),
              "requests_distribution_excluded": "requests" not in selected,
              "target_metadata_closure_verified": True, "package_code_executed": False,
              "packages_installed": False, "runtime_image_prepared": False, "execution_approved": False,
              "repository_execution_enabled": False}
    (output / "requests_harness_dependency_lock.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# Candidate review lock for Linux CPython 3.13; not installation approval.",
             "# Requests itself is excluded: the library under test must come from the selected source snapshot."]
    lines += [f"{row['name']}=={row['version']} --hash=sha256:{row['wheel_sha256']}" for row in rows]
    (output / "requests_harness_requirements.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "packages": pins, "total_wheel_bytes": total,
                      "packages_installed": False, "execution_approved": False}, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output)
