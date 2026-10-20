"""Real Docker job for exact backend-authored arithmetic fixtures only.

This module cannot execute a caller-supplied repository or command. Production
execution_runner remains disabled. Only the two literal fixtures below may run.
"""
from contextlib import ExitStack
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

import artifact_materializer as materializer
from artifact_transfer_probe import PINNED_IMAGE
from isolation_probe import DockerProbe, create_args
from patch_validator import prepare_patch_copy
import source_artifact as policy


BASELINE = b"def add(left, right):\n    return left - right\n"
PATCHED = b"def add(left, right):\n    return left + right\n"
TRUSTED_HASHES = tuple(hashlib.sha256(raw).hexdigest() for raw in (BASELINE, PATCHED))


def fixture_worker_script(digest):
    if not isinstance(digest, str) or not policy.re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid artifact digest")
    code = ["import base64, hashlib, json, re, sys, stat, tempfile, runpy",
            "from pathlib import Path", "from contextlib import contextmanager"]
    for name in ("MAX_FILES", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_ARTIFACT_BYTES", "BLOCKED_PARTS", "BLOCKED_FILES"):
        code.append(name + " = " + repr(getattr(policy, name)))
    code.append("SECRET_MARKERS = re.compile(" + repr(policy.SECRET_MARKERS.pattern) + ", re.IGNORECASE)")
    for function in (policy.approved_path, policy._source_bytes, policy._json_bytes,
                     policy._reject_prefix_collisions, policy.verify_source_artifact,
                     materializer._ordinary_directory, materializer.materialize_source_artifact):
        code.append(inspect.getsource(function))
    code += ["TRUSTED_HASHES = " + repr(TRUSTED_HASHES),
             "payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)",
             "with materialize_source_artifact(payload, " + repr(digest) + ", '/tmp') as (root, manifest):\n"
             "    if [item['path'] for item in manifest['files']] != ['calculator.py']:\n"
             "        raise ValueError('Only the exact trusted fixture is executable')\n"
             "    path = root / 'calculator.py'\n"
             "    if hashlib.sha256(path.read_bytes()).hexdigest() not in TRUSTED_HASHES:\n"
             "        raise ValueError('Source is not a backend-authored fixture')\n"
             "    namespace = runpy.run_path(str(path))\n"
             "    passed = namespace['add'](2, 3) == 5\n"
             "    receipt = {'manifest': manifest, 'synthetic_passed': passed}\n"
             "if root.exists():\n"
             "    raise RuntimeError('Worker input cleanup failed')\n"
             "receipt['materialization_cleanup_verified'] = True\n"
             "print(json.dumps(receipt, sort_keys=True))"]
    return "\n\n".join(code)


def _run_fixture_worker(docker, artifact):
    name = "codepilot-probe-" + uuid.uuid4().hex
    report = {"status": "worker_error", "cleanup_verified": False}
    attempted = False
    try:
        expected = policy.verify_source_artifact(artifact.payload, artifact.sha256)
        args = create_args(PINNED_IMAGE, name, fixture_worker_script(artifact.sha256))
        args.insert(1, "--interactive")
        attempted = True
        if docker.call(*args).returncode:
            raise RuntimeError("Container creation failed")
        config = docker.inspect(name)
        host = config["HostConfig"]
        required = (
            config["Image"] == PINNED_IMAGE,
            config["Config"]["User"] == "65534:65534",
            host["NetworkMode"] == "none", host["ReadonlyRootfs"],
            not host.get("Binds"), not host.get("Privileged"),
            host["Memory"] == 536870912, host["MemorySwap"] == 536870912,
            host["PidsLimit"] == 64, host["NanoCpus"] == 1_000_000_000,
            set(host.get("CapDrop") or []) == {"ALL"},
            "no-new-privileges:true" in host["SecurityOpt"],
            all(mount["Type"] == "tmpfs" for mount in config.get("Mounts", [])),
        )
        if not all(required):
            raise ValueError("Worker configuration does not match the trusted profile")
        result = subprocess.run(docker.command + ["start", "--attach", "--interactive", name],
                                input=artifact.payload, capture_output=True, timeout=30)
        if result.returncode or len(result.stdout) > 128 * 1024:
            raise RuntimeError("Worker receipt unavailable")
        receipt = json.loads(result.stdout)
        state = docker.inspect(name)["State"]
        if state["ExitCode"] != 0 or state.get("OOMKilled"):
            raise RuntimeError("Worker did not complete")
        if (not isinstance(receipt, dict) or set(receipt) != {
            "manifest", "synthetic_passed", "materialization_cleanup_verified"
        } or receipt["manifest"] != expected or type(receipt["synthetic_passed"]) is not bool
                or receipt["materialization_cleanup_verified"] is not True):
            raise ValueError("Invalid worker receipt")
        report.update(status="completed", passed=receipt["synthetic_passed"],
                      manifest=expected, materialization_cleanup_verified=True)
    except subprocess.TimeoutExpired:
        report["status"] = "timeout"
    except Exception as error:
        report["error"] = type(error).__name__
    finally:
        if not attempted:
            report["cleanup_verified"] = True
        if attempted:
            try:
                removed = docker.call("rm", "--force", name)
                listing = docker.call("ps", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}")
                report["cleanup_verified"] = removed.returncode == 0 and listing.returncode == 0 and not listing.stdout.strip()
            except Exception:
                pass
        if not report["cleanup_verified"]:
            report["status"] = "cleanup_error"
    return report


def run_synthetic_job(docker_exe, cancelled=lambda: False):
    """Trusted fixtures only; no repository, patch, command, or image argument."""
    report = {"status": "job_pending", "tested": False, "synthetic_tested": False,
              "repository_execution_enabled": False, "baseline": None, "patched": None,
              "comparison": "not_run", "host_cleanup_verified": False, "image_id": PINNED_IMAGE}
    owned = []
    try:
        with ExitStack() as stack:
            if cancelled():
                report["status"] = "cancelled"
                return report
            docker = DockerProbe(docker_exe)
            image = docker.inspect(PINNED_IMAGE)
            if image.get("Os") != "linux" or image.get("Config", {}).get("Volumes"):
                raise ValueError("Reviewed Linux image required")
            parent = stack.enter_context(tempfile.TemporaryDirectory(prefix="codepilot-job-fixture-"))
            owned.append(Path(parent))
            baseline = Path(parent) / "baseline"
            baseline.mkdir()
            (baseline / "calculator.py").write_bytes(BASELINE)
            edits = [{"file": "calculator.py", "original_code": "return left - right", "replacement_code": "return left + right"}]
            patch_owner, validation = prepare_patch_copy(baseline, edits, ["calculator.py"])
            if patch_owner is not None:
                stack.enter_context(patch_owner)
                owned.append(Path(patch_owner.name))
            if not validation["all_valid"]:
                report["status"] = "validation_failed"
                return report
            patched = Path(patch_owner.name) / "repository"
            for label, snapshot in (("baseline", baseline), ("patched", patched)):
                if cancelled():
                    report["status"] = "cancelled"
                    return report
                artifact = policy.build_source_artifact(snapshot, ["calculator.py"])
                result = _run_fixture_worker(docker, artifact)
                report[label] = result
                if result["status"] != "completed":
                    report["status"] = result["status"]
                    return report
            if cancelled():
                report["status"] = "cancelled"
                return report
            if (baseline / "calculator.py").read_bytes() != BASELINE:
                raise ValueError("Baseline changed during job")
            report["synthetic_tested"] = True
            report["comparison"] = {
                (False, True): "synthetic_failure_to_pass",
                (True, True): "synthetic_no_selected_regression",
                (True, False): "synthetic_possible_regression",
                (False, False): "synthetic_failures_remain",
            }[(report["baseline"]["passed"], report["patched"]["passed"])]
            report["status"] = "synthetic_job_completed"
            return report
    except Exception as error:
        report["status"] = "job_error"
        report["error"] = type(error).__name__
        return report
    finally:
        report["host_cleanup_verified"] = all(not path.exists() for path in owned)
        if not report["host_cleanup_verified"]:
            report["status"] = "cleanup_error"
            report["synthetic_tested"] = False
            report["comparison"] = "not_run"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run_synthetic_job(args.docker)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "synthetic_job_completed" else 1)
