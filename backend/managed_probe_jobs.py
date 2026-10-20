"""Durable, serialized developer-only jobs for fixed trusted Docker probes.

No repository path, script, image, or command can be supplied to this interface.
The production runner is intentionally not connected to this module.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid

from artifact_transfer_probe import PINNED_IMAGE
from isolation_probe import DockerProbe, create_args
from patch_validator import _reject_link
from source_artifact import _json_bytes, verify_source_artifact
from synthetic_worker_job import BASELINE, PATCHED, fixture_worker_script


OUTPUT_LIMIT = 1_048_576
CAPTURE_LIMIT = 65_536


class JobJournal:
    """One OS-locked controller per journal; committed intent precedes creation."""
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.connection = None
        self.lock = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        _reject_link(self.root)
        lock_path = self.root / "controller.lock"
        if lock_path.exists():
            _reject_link(lock_path)
        self.lock = lock_path.open("a+b")
        if self.lock.tell() == 0:
            self.lock.write(b"0")
            self.lock.flush()
        self.lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            self.lock = None
            raise RuntimeError("Another controller holds this journal") from None
        try:
            path = self.root / "jobs.sqlite3"
            if path.exists():
                _reject_link(path)
            self.connection = sqlite3.connect(path, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS meta (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                    result TEXT, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS resources (
                    name TEXT PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id),
                    state TEXT NOT NULL);
            ''')
            row = self.connection.execute("SELECT id FROM meta").fetchone()
            self.journal_id = row["id"] if row else uuid.uuid4().hex
            if not re.fullmatch(r"[0-9a-f]{32}", self.journal_id):
                raise ValueError("Invalid journal identity")
            if row is None:
                self.connection.execute("INSERT INTO meta VALUES (?)", (self.journal_id,))
            self.connection.commit()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self.connection:
                self.connection.close()
        finally:
            self.connection = None
            if self.lock:
                if os.name == "nt":
                    import msvcrt
                    self.lock.seek(0)
                    msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.lock, fcntl.LOCK_UN)
                self.lock.close()
                self.lock = None

    def reserve(self, kind):
        if kind not in {"baseline", "patched", "sleep", "flood", "orphan"}:
            raise ValueError("Only fixed trusted probes are supported")
        job = uuid.uuid4().hex
        name = "codepilot-probe-" + uuid.uuid4().hex
        with self.connection:
            self.connection.execute("INSERT INTO jobs VALUES (?, ?, 'planned', NULL, ?)", (job, kind, time.time()))
            self.connection.execute("INSERT INTO resources VALUES (?, ?, 'planned')", (name, job))
        return job, name

    def finish(self, job, result):
        with self.connection:
            self.connection.execute("UPDATE jobs SET state=?, result=?, updated=? WHERE id=?",
                                    (result["status"], json.dumps(result), time.time(), job))


def _cleanup(journal, docker, job, name):
    """Remove only a resource whose immutable container identity is owned."""
    if not re.fullmatch(r"codepilot-probe-[0-9a-f]{32}", name) or not re.fullmatch(r"[0-9a-f]{32}", job):
        return False
    try:
        listed = docker.call("ps", "--all", "--no-trunc", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}")
        if listed.returncode:
            return False
        container = listed.stdout.decode("ascii").strip()
        if container:
            if not re.fullmatch(r"[0-9a-f]{64}", container):
                return False
            config = docker.inspect(container)
            labels = config.get("Config", {}).get("Labels", {}) or {}
            if labels.get("codepilot.job") != job or labels.get("codepilot.journal") != journal.journal_id:
                return False
            if config.get("Id") != container:
                return False
            if docker.call("rm", "--force", container).returncode:
                return False
            after = docker.call("ps", "--all", "--no-trunc", "--filter", "id=" + container, "--format", "{{.ID}}")
            if after.returncode or after.stdout.strip():
                return False
        with journal.connection:
            journal.connection.execute("UPDATE resources SET state='cleaned' WHERE name=? AND job=?", (name, job))
        return True
    except Exception:
        return False


def recover(journal, docker):
    recovered = []
    # Reconcile recorded names even after a previously observed absence. A Docker
    # create request may finish late after its client times out.
    rows = journal.connection.execute("SELECT r.name, r.job, r.state, j.state AS job_state FROM resources r JOIN jobs j ON r.job=j.id").fetchall()
    for row in rows:
        if row["state"] == "cleaned":
            try:
                listed = docker.call("ps", "--all", "--no-trunc", "--filter", "name=^/" + row["name"] + "$", "--format", "{{.ID}}")
                if listed.returncode == 0 and not listed.stdout.strip() and row["job_state"] not in ("planned", "running"):
                    continue
            except Exception:
                pass  # _cleanup reports unavailable Docker as a failure.
        clean = _cleanup(journal, docker, row["job"], row["name"])
        result = {"job_id": row["job"], "status": "recovered" if clean else "cleanup_error",
                  "cleanup_verified": clean, "repository_execution_enabled": False}
        journal.finish(row["job"], result)
        recovered.append(result)
    return recovered


def _program(kind):
    if kind in ("baseline", "patched"):
        raw = BASELINE if kind == "baseline" else PATCHED
        document = {"version": 1, "files": [{"path": "calculator.py", "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "content_b64": base64.b64encode(raw).decode()}]}
        payload = _json_bytes(document)
        digest = hashlib.sha256(payload).hexdigest()
        return fixture_worker_script(digest), payload, verify_source_artifact(payload, digest)
    if kind in ("sleep", "orphan"):
        return "import time; print('ready', flush=True); time.sleep(120)", b"", None
    if kind == "flood":
        return "import os\nwhile True: os.write(1, b'x' * 65536)", b"", None
    raise ValueError("Unknown fixed probe")


def _create(journal, docker, job, name, kind):
    script, payload, manifest = _program(kind)
    args = create_args(PINNED_IMAGE, name, script)
    args[1:1] = ["--interactive", "--label", "codepilot.job=" + job,
                 "--label", "codepilot.journal=" + journal.journal_id]
    if docker.call(*args).returncode:
        raise RuntimeError("Worker creation failed")
    config = docker.inspect(name)
    host = config["HostConfig"]
    if not all((config["Image"] == PINNED_IMAGE, config["Config"]["User"] == "65534:65534",
                host["NetworkMode"] == "none", host["ReadonlyRootfs"],
                not host.get("Binds"), not host.get("Privileged"),
                host["Memory"] == 536870912, host["MemorySwap"] == 536870912,
                host["PidsLimit"] == 64, host["NanoCpus"] == 1000000000,
                set(host.get("CapDrop") or []) == {"ALL"},
                "no-new-privileges:true" in host["SecurityOpt"],
                all(mount["Type"] == "tmpfs" for mount in config.get("Mounts", [])))):
        raise ValueError("Worker profile mismatch")
    with journal.connection:
        journal.connection.execute("UPDATE resources SET state='created' WHERE name=?", (name,))
        journal.connection.execute("UPDATE jobs SET state='running', updated=? WHERE id=?", (time.time(), job))
    return payload, manifest


def run_managed_probe(journal, docker, kind, cancelled=lambda: False, timeout_seconds=30):
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 60:
        raise ValueError("Probe timeout must be between zero and 60 seconds")
    # Do not start more work while a prior resource still needs recovery.
    if journal.connection.execute("SELECT 1 FROM resources WHERE state != 'cleaned' LIMIT 1").fetchone():
        raise RuntimeError("Pending resources must be recovered first")
    job, name = journal.reserve(kind)
    result = {"job_id": job, "status": "worker_error", "cleanup_verified": False,
              "output_bytes": 0, "capture_bytes": 0, "repository_execution_enabled": False}
    process = None
    reader = writer = None
    captured = bytearray()
    total = [0]
    exceeded = threading.Event()
    pipe_error = threading.Event()
    uncertain_create = False
    try:
        if cancelled():
            result["status"] = "cancelled"
        else:
            payload, manifest = _create(journal, docker, job, name, kind)
            process = subprocess.Popen(docker.command + ["start", "--attach", "--interactive", name],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            def drain():
                try:
                    while chunk := process.stdout.read(8192):
                        total[0] += len(chunk)
                        captured.extend(chunk[:max(0, CAPTURE_LIMIT - len(captured))])
                        if total[0] > OUTPUT_LIMIT:
                            exceeded.set()
                            process.stdout.close()
                            return
                except Exception:
                    pipe_error.set()

            def feed():
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except Exception:
                    pipe_error.set()

            reader = threading.Thread(target=drain, daemon=True)
            writer = threading.Thread(target=feed, daemon=True)
            reader.start()
            writer.start()
            deadline = time.monotonic() + timeout_seconds
            while True:
                if cancelled():
                    result["status"] = "cancelled"
                    break
                if exceeded.is_set():
                    result["status"] = "output_limit_exceeded"
                    break
                if time.monotonic() >= deadline:
                    result["status"] = "timeout"
                    break
                if process.poll() is not None:
                    reader.join(timeout=5)
                    writer.join(timeout=5)
                    if exceeded.is_set():
                        result["status"] = "output_limit_exceeded"
                    elif reader.is_alive() or writer.is_alive() or pipe_error.is_set():
                        result["status"] = "worker_error"
                    else:
                        state = docker.inspect(name)["State"]
                        if state.get("OOMKilled"):
                            result["status"] = "resource_limit"
                        elif state["ExitCode"] == 0 and total[0] <= CAPTURE_LIMIT:
                            if manifest is not None:
                                receipt = json.loads(captured)
                                if (receipt.get("manifest") != manifest or type(receipt.get("synthetic_passed")) is not bool
                                        or receipt.get("materialization_cleanup_verified") is not True):
                                    raise ValueError("Invalid fixture receipt")
                                result["synthetic_passed"] = receipt["synthetic_passed"]
                            result["status"] = "completed"
                        elif total[0] > CAPTURE_LIMIT:
                            result["status"] = "capture_limit_exceeded"
                    break
                exceeded.wait(0.05)
    except Exception as error:
        result["error"] = type(error).__name__
        uncertain_create = isinstance(error, subprocess.TimeoutExpired) and process is None
    finally:
        result["cleanup_verified"] = _cleanup(journal, docker, job, name)
        if uncertain_create:
            # The daemon may still finish an accepted create request later.
            with journal.connection:
                journal.connection.execute("UPDATE resources SET state='uncertain' WHERE name=?", (name,))
            result["cleanup_verified"] = False
        if process:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()  # CLI only; unresolved container remains in the journal.
                process.wait(timeout=5)
            for thread in (reader, writer):
                if thread:
                    thread.join(timeout=5)
            if any(thread and thread.is_alive() for thread in (reader, writer)):
                result["cleanup_verified"] = False
            for stream in (process.stdin, process.stdout):
                if stream and not stream.closed:
                    stream.close()
        result["output_bytes"] = total[0]
        result["capture_bytes"] = len(captured)
        if not result["cleanup_verified"]:
            result["status"] = "cleanup_error"
        journal.finish(job, result)
    return result


def leave_recovery_fixture(journal, docker):
    """Create a known sleeping worker for the explicit crash/recovery check."""
    job, name = journal.reserve("orphan")
    _create(journal, docker, job, name, "orphan")
    if docker.call("start", name).returncode:
        _cleanup(journal, docker, job, name)
        raise RuntimeError("Recovery fixture did not start")
    return {"job_id": job, "container": name, "status": "recovery_fixture_running"}


def live_checks(root, docker_exe):
    with JobJournal(root) as journal:
        docker = DockerProbe(docker_exe)
        image = docker.inspect(PINNED_IMAGE)
        if image.get("Os") != "linux" or image.get("Config", {}).get("Volumes"):
            raise ValueError("Reviewed Linux image required")
        recovery = recover(journal, docker)
        if any(not row["cleanup_verified"] for row in recovery):
            return {"status": "cleanup_error", "recovery": recovery}
        baseline = run_managed_probe(journal, docker, "baseline")
        patched = run_managed_probe(journal, docker, "patched")
        cancelled = threading.Event()
        timer = threading.Timer(2, cancelled.set)
        timer.start()
        try:
            cancellation = run_managed_probe(journal, docker, "sleep", cancelled=cancelled.is_set)
        finally:
            timer.cancel()
        timeout = run_managed_probe(journal, docker, "sleep", timeout_seconds=2)
        output = run_managed_probe(journal, docker, "flood")
        checks = {"baseline": baseline, "patched": patched, "active_cancellation": cancellation,
                  "timeout": timeout, "output_limit": output}
        passed = (baseline.get("synthetic_passed") is False and patched.get("synthetic_passed") is True
                  and baseline["status"] == patched["status"] == "completed"
                  and cancellation["status"] == "cancelled" and timeout["status"] == "timeout"
                  and cancellation["output_bytes"] > 0
                  and output["status"] == "output_limit_exceeded"
                  and all(row["cleanup_verified"] for row in checks.values()))
        return {"status": "managed_checks_passed" if passed else "check_failed", "recovery": recovery,
                "checks": checks, "repository_execution_enabled": False, "tested": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--mode", choices=("check", "crash-fixture"), default="check")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.mode == "crash-fixture":
        with JobJournal(args.root) as journal:
            result = leave_recovery_fixture(journal, DockerProbe(args.docker))
            print(json.dumps(result), flush=True)
            # Deliberately bypass finally: emulate abrupt service termination.
            os._exit(0)
    result = live_checks(args.root, args.docker)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "managed_checks_passed" else 1)
