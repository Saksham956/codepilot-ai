"""Check receipt consistency without importing any selected source module."""
from pathlib import PurePosixPath
import re

from source_artifact import verify_source_artifact


TARGET_MODULES = {"requests": "src/requests/__init__.py", "requests.auth": "src/requests/auth.py"}


def check_import_receipt(receipt, payload, expected_sha256, expected_worker_root):
    """Future trusted worker must produce observations; this checker does not.

    Covers only Requests' entry module and auth module. Dependency aliases and
    other modules need their own reviewed provenance rules before execution.
    """
    report = {"status": "provenance_inconsistent", "target_modules_consistent": False,
              "runtime_imports_verified": False, "repository_execution_enabled": False,
              "checked_modules": [], "checker_imported_repository_code": False}
    try:
        manifest = verify_source_artifact(payload, expected_sha256)
        if type(expected_worker_root) is not str or not re.fullmatch(r"/tmp/codepilot-input-[A-Za-z0-9_-]{1,100}", expected_worker_root):
            raise ValueError("Unexpected worker input root")
        if type(receipt) is not dict or set(receipt) != {"artifact_sha256", "package_paths", "modules"}:
            raise ValueError("Unexpected receipt schema")
        if receipt["artifact_sha256"] != expected_sha256:
            raise ValueError("Receipt identifies a different artifact")
        package_path = expected_worker_root + "/src/requests"
        if receipt["package_paths"] != [package_path]:
            raise ValueError("Package search path contains an unexpected location")
        rows = receipt["modules"]
        if type(rows) is not list or len(rows) != len(TARGET_MODULES):
            raise ValueError("Both target module observations are required")
        files = {entry["path"]: entry for entry in manifest["files"]}
        seen = set()
        for row in rows:
            if type(row) is not dict or set(row) != {"name", "origin", "sha256"}:
                raise ValueError("Invalid observed module")
            name = row["name"]
            if type(name) is not str or name not in TARGET_MODULES or name in seen:
                raise ValueError("Unexpected or duplicate target module")
            relative = TARGET_MODULES[name]
            origin = row["origin"]
            if type(origin) is not str or str(PurePosixPath(origin)) != origin or ".." in PurePosixPath(origin).parts:
                raise ValueError("Origin is not canonical")
            if origin != expected_worker_root + "/" + relative:
                raise ValueError("Target was not imported from its expected materialized file")
            if relative not in files or row["sha256"] != files[relative]["sha256"]:
                raise ValueError("Observed source hash differs from the selected snapshot")
            seen.add(name)
        report.update(status="receipt_consistent_not_runtime_verified", target_modules_consistent=True,
                      checked_modules=sorted(seen))
    except (ValueError, TypeError, KeyError, RecursionError):
        pass
    return report
