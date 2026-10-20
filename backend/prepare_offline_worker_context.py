"""Create a minimal Docker context from already-verified wheel data only."""
import hashlib
import json
from pathlib import Path
import shutil

from artifact_transfer_probe import PINNED_IMAGE
from verify_requests_dependency_lock import verify_lock


def prepare(inputs, destination):
    result = verify_lock(inputs)
    if not result["metadata_closure_verified"]:
        raise ValueError("Dependency inputs did not pass offline verification")
    lock = json.loads((inputs / "requests_harness_dependency_lock.json").read_text())
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "wheels").mkdir()
    for row in lock["dependencies"]:
        shutil.copyfile(inputs / "wheels" / row["filename"], destination / "wheels" / row["filename"])
    shutil.copyfile(inputs / "requests_harness_requirements.lock", destination / "requirements.lock")
    dockerfile = f'''FROM python@{PINNED_IMAGE}
LABEL codepilot.review_worker="true" codepilot.dependency_lock="{lock['dependency_lock_sha256']}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS="" PYTEST_PLUGINS=""
COPY requirements.lock /opt/requirements.lock
COPY wheels /opt/wheels
RUN python -I -m venv /opt/worker-venv && /opt/worker-venv/bin/python -I -m pip install --isolated --no-index --no-cache-dir --only-binary=:all: --require-hashes --no-deps --find-links=/opt/wheels -r /opt/requirements.lock && /opt/worker-venv/bin/python -I -m pip check
ENV PATH="/opt/worker-venv/bin:$PATH"
USER 65534:65534
WORKDIR /tmp
'''
    (destination / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (destination / ".dockerignore").write_text("*\n!Dockerfile\n!requirements.lock\n!wheels/\n!wheels/*.whl\n", encoding="utf-8")
    manifest = {"base_image": PINNED_IMAGE, "dependency_lock_sha256": lock["dependency_lock_sha256"],
                "files": [{"path": path.relative_to(destination).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                          for path in sorted(destination.rglob("*")) if path.is_file()],
                "repository_files_included": False, "secrets_included": False}
    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.inputs, args.destination)
    args.manifest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Minimal build context prepared; no build or installation performed yet")
