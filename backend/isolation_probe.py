"""Developer-only Docker checks with fixed trusted programs; no repository input.

Passing probes does not enable execution_runner or certify hostile-code safety.
Image preparation is an explicit separate action; this module never pulls images.
"""
import json
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid


CHECKS = {
    "permissions": r'''
import json, os, pathlib, socket
status = pathlib.Path('/proc/self/status').read_text()
assert os.getuid() == 65534
assert 'CapEff:\t0000000000000000' in status
assert 'NoNewPrivs:\t1' in status
assert 'Seccomp:\t2' in status
mounts = pathlib.Path('/proc/mounts').read_text().splitlines()
tmp_options = next(line.split()[3].split(',') for line in mounts if line.split()[1] == '/tmp')
assert {'noexec', 'nosuid', 'nodev'} <= set(tmp_options)
assert set(os.listdir('/sys/class/net')) == {'lo'}
s = socket.socket(); s.settimeout(1)
try:
    s.connect(('198.18.0.1', 9))
except OSError:
    pass
else:
    raise AssertionError('Unexpected network connection')
finally:
    s.close()
for name in ['GEMINI_API_KEY', 'OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'CODEPILOT_HOST_SENTINEL']:
    assert name not in os.environ
for location in ['/var/run/docker.sock', '/run/docker.sock', '/host', '/workspace', '/mnt/host']:
    assert not pathlib.Path(location).exists()
try:
    pathlib.Path('/root-write-probe').write_text('deny')
except OSError:
    pass
else:
    raise AssertionError('Root filesystem was writable')
pathlib.Path('/tmp/scratch').write_text('allowed')
assert pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip() == '536870912'
assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip() == '64'
quota, period = pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().split()
assert int(quota) == int(period)
print(json.dumps({'permissions': 'passed'}))
''',
    "scratch_limit": r'''
import errno, pathlib
try:
    with open('/tmp/fill', 'wb', buffering=0) as stream:
        for _ in range(260):
            stream.write(b'x' * 1048576)
except OSError as error:
    assert error.errno == errno.ENOSPC
else:
    raise AssertionError('Scratch limit not enforced')
print('scratch_limit passed')
''',
    "process_limit": r'''
import errno, subprocess, sys
children = []
try:
    try:
        for _ in range(70):
            children.append(subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(20)']))
    except OSError as error:
        assert error.errno == errno.EAGAIN
    else:
        raise AssertionError('Process limit not enforced')
finally:
    for child in children:
        child.terminate()
    for child in children:
        child.wait()
print('process_limit passed')
''',
    "memory_limit": "data = bytearray(768 * 1024 * 1024)",
    "timeout": "import time; time.sleep(120)",
    "output_limit": "import os; data = b'x' * 65536\nwhile True: os.write(1, data)",
}


def create_args(image_id, name, script):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Use an inspected immutable local image ID")
    if not re.fullmatch(r"codepilot-probe-[0-9a-f]{32}", name):
        raise ValueError("Unexpected probe name")
    args = ["create", "--name", name, "--label", "codepilot.trusted-probe=true",
            "--pull=never", "--network=none", "--read-only", "--user=65534:65534",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--pids-limit=64", "--cpus=1", "--memory=512m", "--memory-swap=512m",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=268435456,mode=1777",
            "--shm-size=16m", "--ipc=private", "--cgroupns=private",
            "--ulimit=nofile=128:128", "--log-driver=none", "--no-healthcheck"]
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        args += ["--env", key + "="]
    return args + ["--entrypoint=python", image_id, "-I", "-B", "-c", script]


class DockerProbe:
    def __init__(self, docker_exe, context="desktop-linux"):
        self.command = [str(docker_exe), "--context", context]

    def call(self, *args, timeout=15):
        return subprocess.run(self.command + list(args), capture_output=True, timeout=timeout)

    def inspect(self, name):
        result = self.call("inspect", name)
        if result.returncode:
            raise RuntimeError("Docker inspect failed")
        return json.loads(result.stdout)[0]

    def one(self, image_id, kind):
        name = "codepilot-probe-" + uuid.uuid4().hex
        process = None
        reader = None
        report = {"probe": kind, "passed": False, "cleanup_verified": False}
        try:
            result = self.call(*create_args(image_id, name, CHECKS[kind]))
            if result.returncode:
                raise RuntimeError("Docker create failed")
            config = self.inspect(name)
            host = config["HostConfig"]
            assert config["Image"] == image_id
            assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
            assert host["Memory"] == 536870912 and host["MemorySwap"] == 536870912
            assert host["PidsLimit"] == 64 and host["NanoCpus"] == 1_000_000_000
            assert config["Config"]["User"] == "65534:65534"
            assert not host.get("Binds") and not host.get("Privileged")
            assert set(host.get("CapDrop") or []) == {"ALL"}
            assert "no-new-privileges:true" in host["SecurityOpt"]
            assert all(mount["Type"] == "tmpfs" for mount in config.get("Mounts", []))
            process = subprocess.Popen(self.command + ["start", "--attach", name],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            exceeded = threading.Event()
            received = [0]

            def drain():
                while chunk := process.stdout.read(8192):
                    received[0] += len(chunk)
                    if received[0] > 1_048_576:
                        exceeded.set()
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            deadline = time.monotonic() + (2 if kind == "timeout" else 30)
            outcome = "completed"
            while process.poll() is None:
                if exceeded.is_set():
                    outcome = "output_limit_exceeded"
                    break
                if time.monotonic() >= deadline:
                    outcome = "timeout"
                    break
                time.sleep(0.05)
            if outcome == "completed":
                process.wait(timeout=5)
                reader.join(timeout=5)
                if reader.is_alive():
                    raise RuntimeError("Output reader did not finish")
                if exceeded.is_set():
                    outcome = "output_limit_exceeded"
                state = self.inspect(name)["State"]
                report["exit_code"] = state["ExitCode"]
                report["oom_killed"] = state["OOMKilled"]
                if kind == "memory_limit":
                    report["passed"] = state["OOMKilled"] and state["ExitCode"] != 0
                else:
                    report["passed"] = state["ExitCode"] == 0 and kind not in ("timeout", "output_limit")
            if kind == "timeout":
                report["passed"] = outcome == "timeout"
            if kind == "output_limit":
                report["passed"] = outcome == "output_limit_exceeded"
            report["outcome"] = outcome
        except Exception as error:
            report["error"] = type(error).__name__
        finally:
            # Only remove the unique name created for this probe; never prune.
            try:
                removed = self.call("rm", "--force", name)
                if process is not None:
                    process.wait(timeout=10)
                    if reader is not None:
                        reader.join(timeout=5)
                        if reader.is_alive():
                            raise RuntimeError("Output reader did not stop")
                    if process.stdout:
                        process.stdout.close()
                listing = self.call("ps", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}")
                report["cleanup_verified"] = removed.returncode == 0 and listing.returncode == 0 and not listing.stdout.strip()
            except Exception:
                report["cleanup_verified"] = False
            report["passed"] = bool(report["passed"] and report["cleanup_verified"])
        return report

    def run(self, image_id):
        engine = self.call("info", "--format", "{{.OSType}}")
        if engine.returncode or engine.stdout.strip() != b"linux":
            return {"status": "runtime_unavailable", "repository_execution_enabled": False, "probes": []}
        image = self.inspect(image_id)
        if image.get("Os") != "linux" or image.get("Config", {}).get("Volumes"):
            raise ValueError("Expected a Linux Python image without declared volumes")
        reports = []
        for kind in CHECKS:
            result = self.one(image_id, kind)
            reports.append(result)
            if not result["passed"]:
                break
        return {"status": "synthetic_probes_passed" if len(reports) == len(CHECKS) and all(r["passed"] for r in reports) else "probe_failed",
                "image_id": image_id, "repository_execution_enabled": False, "probes": reports}


if __name__ == "__main__":
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    # A harmless host-side marker proves host environment isn't passed through.
    # This is intentionally never provided using Docker's --env option.
    os.environ["CODEPILOT_HOST_SENTINEL"] = "synthetic-host-only-marker"
    result = DockerProbe(args.docker).run(args.image_id)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "synthetic_probes_passed" else 1)
