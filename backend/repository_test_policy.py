"""Static repository-plan checks only. No installs, collection, or execution."""
import hashlib
import json
import re
from types import MappingProxyType

from source_artifact import approved_path, verify_source_artifact


# Human-reviewed profiles would be backend-owned, never supplied by API input.
# None have been approved. This module intentionally contains no launcher.
REVIEWED_REPOSITORY_PROFILES = MappingProxyType({})
REQUIRED = {
    "schema_version", "repository", "revision", "artifact_sha256", "worker_image",
    "dependency_lock_sha256", "dependencies", "test_nodes",
}


def _digest(value, label):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(label + " must be a lowercase SHA-256 digest")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def dependency_lock_digest(dependencies):
    """Identity of the declared pin list, not proof of installed wheel contents."""
    return hashlib.sha256(_canonical(sorted(dependencies, key=lambda item: item["name"]))).hexdigest()


def _validated_plan(plan, artifact_manifest):
    if type(plan) is not dict or set(plan) != REQUIRED:
        raise ValueError("Plan fields must match the fixed schema; commands and override flags are not accepted")
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
        raise ValueError("Unknown plan schema version")
    repository = plan["repository"]
    if type(repository) is not str or len(repository) > 200 or not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", repository
    ) or repository.endswith((".git", ".")):
        raise ValueError("Use a canonical HTTPS GitHub owner/repository URL without credentials or query strings")
    revision = plan["revision"]
    if type(revision) is not str or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
        raise ValueError("Use a full commit object ID, not a branch or tag")
    _digest(plan["artifact_sha256"], "Artifact identity")
    _digest(plan["dependency_lock_sha256"], "Dependency lock identity")
    if plan["artifact_sha256"] != artifact_manifest["artifact_sha256"]:
        raise ValueError("Plan does not identify the verified source artifact")
    image = plan["worker_image"]
    if type(image) is not str or not image.startswith("sha256:"):
        raise ValueError("Worker image must use an immutable local image ID")
    _digest(image[7:], "Worker image identity")

    dependencies = plan["dependencies"]
    if type(dependencies) is not list or not 1 <= len(dependencies) <= 100:
        raise ValueError("Declare a bounded exact dependency list")
    names = set()
    normalized_dependencies = []
    for item in dependencies:
        if type(item) is not dict or set(item) != {"name", "version", "wheel_sha256"}:
            raise ValueError("Each dependency requires only a name, exact version, and wheel digest")
        name, version = item["name"], item["version"]
        if type(name) is not str or len(name) > 100 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError("Dependency names must use lowercase canonical package names")
        if name in names:
            raise ValueError("Duplicate dependency names are not permitted")
        names.add(name)
        if type(version) is not str or not re.fullmatch(r"[0-9][0-9A-Za-z.!+_-]{0,63}", version):
            raise ValueError("Use an exact version token; ranges, URLs, paths, and editable installs are not permitted")
        _digest(item["wheel_sha256"], "Wheel identity")
        normalized_dependencies.append(dict(item))
    if "pytest" not in names:
        raise ValueError("This policy supports only an explicitly declared pytest-based plan")
    if dependency_lock_digest(normalized_dependencies) != plan["dependency_lock_sha256"]:
        raise ValueError("Dependency lock digest does not match the declared pins")

    tests = plan["test_nodes"]
    if type(tests) is not list or not 1 <= len(tests) <= 20:
        raise ValueError("Select between 1 and 20 explicit test nodes")
    files = {entry["path"] for entry in artifact_manifest["files"]}
    selected = set()
    for node in tests:
        if type(node) is not str or len(node) > 400:
            raise ValueError("Invalid test node")
        components = node.split("::")
        if len(components) not in (2, 3):
            raise ValueError("Select explicit test functions, optionally inside a class")
        filename = approved_path(components[0])
        if filename != components[0] or not filename.startswith("tests/") or not filename.split("/")[-1].startswith("test_"):
            raise ValueError("Select canonical tests/test_*.py paths")
        if filename not in files:
            raise ValueError("Selected test file is absent from the verified artifact")
        if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", component) for component in components[1:]):
            raise ValueError("Test nodes cannot contain options, expressions, wildcards, or parameter selectors")
        if not components[-1].startswith("test_"):
            raise ValueError("Select an explicit test function")
        if len(components) == 3 and not components[1].startswith("Test"):
            raise ValueError("Select an explicit pytest-style test class")
        if node in selected:
            raise ValueError("Duplicate test nodes are not permitted")
        selected.add(node)
    return {**plan, "dependencies": sorted(normalized_dependencies, key=lambda item: item["name"]), "test_nodes": sorted(selected)}


def preflight_repository_plan(plan, payload, expected_artifact_sha256):
    """Check data consistency, never grant approval or run repository programs.

    The expected artifact digest must come from a trusted caller/job record.
    Declared commits and dependencies are not verified against external systems.
    """
    report = {
        "plan_valid": False, "review_status": "not_reviewed", "plan_sha256": None,
        "execution_status": "execution_disabled", "eligible_for_execution": False,
        "tested": False, "repository_execution_enabled": False, "blockers": [],
    }
    try:
        manifest = verify_source_artifact(payload, expected_artifact_sha256)
        normalized = _validated_plan(plan, manifest)
        report.update(plan_valid=True, plan_sha256=hashlib.sha256(_canonical(normalized)).hexdigest(),
                      selected_test_count=len(normalized["test_nodes"]), dependency_count=len(normalized["dependencies"]))
        report["blockers"] = [
            {"code": "repository_profile_not_reviewed", "message": "No repository execution profile has been approved"},
            {"code": "source_provenance_unverified", "message": "The artifact is consistent, but its declared commit origin still needs trusted verification"},
            {"code": "dependency_image_unverified", "message": "Wheel availability, dependency closure, lock contents, and image contents have not been verified"},
            {"code": "test_collection_not_reviewed", "message": "Test imports, fixtures, hooks, plugins, configuration, and network needs still require review"},
            {"code": "repository_execution_disabled", "message": "The production repository executor is disabled"},
        ]
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        # Avoid echoing source, package contents, or attacker-provided strings.
        report["blockers"] = [{"code": "invalid_plan_or_artifact", "message": "Plan or source artifact failed static validation"}]
    return report
