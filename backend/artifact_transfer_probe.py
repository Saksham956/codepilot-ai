"""Fixed synthetic transfer check; never imports the transferred source."""
import inspect
import json
from pathlib import Path
import tempfile
import uuid

from isolation_probe import DockerProbe, create_args
import source_artifact as policy


PINNED_IMAGE = "sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f"


def receiver_script(digest):
    """Ship trusted verifier code, not selected repository source as code."""
    if not isinstance(digest, str) or not policy.re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid expected digest")
    code = ["import base64, hashlib, json, re, sys"]
    for name in ("MAX_FILES", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_ARTIFACT_BYTES", "BLOCKED_PARTS", "BLOCKED_FILES"):
        code.append(name + " = " + repr(getattr(policy, name)))
    code.append("SECRET_MARKERS = re.compile(" + repr(policy.SECRET_MARKERS.pattern) + ", re.IGNORECASE)")
    for function in (policy.approved_path, policy._source_bytes, policy._json_bytes,
                     policy._reject_prefix_collisions, policy.verify_source_artifact):
        code.append(inspect.getsource(function))
    code += ["payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)",
             "manifest = verify_source_artifact(payload, " + repr(digest) + ")",
             "print(json.dumps(manifest, sort_keys=True))"]
    return "\n\n".join(code)


def run_synthetic_transfer(docker_exe):
    """No caller-selected source, commands, mounts, or image parameters."""
    docker = DockerProbe(docker_exe)
    name = "codepilot-probe-" + uuid.uuid4().hex
    report = {"status": "probe_failed", "repository_execution_enabled": False,
              "source_executed": False, "cleanup_verified": False, "image_id": PINNED_IMAGE}
    attempted = False
    try:
        image = docker.inspect(PINNED_IMAGE)
        if image.get("Os") != "linux" or image.get("Config", {}).get("Volumes"):
            raise ValueError("Expected reviewed Linux image without image volumes")
        with tempfile.TemporaryDirectory(prefix="codepilot-transfer-fixture-") as directory:
            root = Path(directory)
            (root / "sample.py").write_bytes(b"raise RuntimeError('SOURCE MUST NOT RUN')\r\nvalue = 1\r\n")
            (root / ".env").write_text("SYNTHETIC_EXCLUDED_VALUE", encoding="utf-8")
            (root / "unselected.py").write_text("raise RuntimeError('UNSELECTED')\n", encoding="utf-8")
            artifact = policy.build_source_artifact(root, ["sample.py"])
            expected = policy.verify_source_artifact(artifact.payload, artifact.sha256)
            command = create_args(PINNED_IMAGE, name, receiver_script(artifact.sha256))
            command.insert(1, "--interactive")
            attempted = True
            created = docker.call(*command)
            if created.returncode:
                raise RuntimeError("Container creation failed")
            config = docker.inspect(name)
            host = config["HostConfig"]
            assert config["Image"] == PINNED_IMAGE
            assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
            assert config["Config"]["User"] == "65534:65534"
            assert not host.get("Binds") and not host.get("Privileged")
            assert all(mount["Type"] == "tmpfs" for mount in config.get("Mounts", []))
            # Only a fixed trusted verifier runs. Payload source is never eval'd,
            # compiled, imported, or extracted. Input and receipt are bounded.
            import subprocess
            result = subprocess.run(docker.command + ["start", "--attach", "--interactive", name],
                                    input=artifact.payload, capture_output=True, timeout=30)
            if result.returncode or len(result.stdout) > 128 * 1024:
                raise RuntimeError("Transfer verifier failed")
            receipt = json.loads(result.stdout)
            state = docker.inspect(name)["State"]
            if state["ExitCode"] != 0 or receipt != expected:
                raise ValueError("Worker manifest mismatch")
            report.update(status="synthetic_transfer_passed", manifest=receipt,
                          payload_bytes=len(artifact.payload), selected_files_only=True)
    except Exception as error:
        report["error"] = type(error).__name__
    finally:
        if attempted:
            try:
                removed = docker.call("rm", "--force", name)
                listed = docker.call("ps", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}")
                report["cleanup_verified"] = removed.returncode == 0 and listed.returncode == 0 and not listed.stdout.strip()
            except Exception:
                report["cleanup_verified"] = False
        if not report["cleanup_verified"]:
            report["status"] = "probe_failed"
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run_synthetic_transfer(args.docker)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "synthetic_transfer_passed" else 1)
