"""Synthetic data-only example; placeholder pins are not real dependencies."""
import json
from pathlib import Path
import tempfile

from repository_test_policy import dependency_lock_digest, preflight_repository_plan
from source_artifact import build_source_artifact


def synthetic_example():
    with tempfile.TemporaryDirectory(prefix="codepilot-preflight-") as directory:
        root = Path(directory)
        (root / "tests").mkdir()
        (root / "calculator.py").write_text("raise RuntimeError('DO NOT EXECUTE')\n", encoding="utf-8")
        (root / "tests/test_add.py").write_text("def test_add():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        artifact = build_source_artifact(root, ["calculator.py", "tests/test_add.py"])
    # Clearly synthetic placeholders: this is not an approved real repository.
    plan = {"schema_version": 1, "repository": "https://github.com/example/synthetic-fixture",
            "revision": "1" * 40, "artifact_sha256": artifact.sha256,
            "worker_image": "sha256:" + "2" * 64, "dependency_lock_sha256": "3" * 64,
            "dependencies": [{"name": "pytest", "version": "0.0.0", "wheel_sha256": "4" * 64}],
            "test_nodes": ["tests/test_add.py::test_add"]}
    plan["dependency_lock_sha256"] = dependency_lock_digest(plan["dependencies"])
    return preflight_repository_plan(plan, artifact.payload, artifact.sha256)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = synthetic_example()
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["plan_valid"] and not result["eligible_for_execution"] else 1)
