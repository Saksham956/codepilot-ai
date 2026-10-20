"""Single-operator, serialized synthetic job service; no repository executor."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from isolation_probe import DockerProbe
from managed_probe_jobs import JobJournal, recover, run_managed_probe
from patch_validator import _reject_link
from synthetic_execution_policy import selected_profile, policy_snapshot


class ServiceUnavailable(RuntimeError):
    pass


class QueueFull(RuntimeError):
    pass


class SyntheticJobService:
    def __init__(self, root, docker_exe):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        _reject_link(self.root)
        self.docker_exe = str(docker_exe)
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.recovery_ok = False
        self.closed = False
        # A lifetime OS lease refuses a second API process using this runtime.
        self.lease = JobJournal(self.root / "api-owner")
        self.lease.__enter__()
        try:
            self.database = self.root / "api-jobs.sqlite3"
            if self.database.exists():
                _reject_link(self.database)
            with self.connect() as connection:
                connection.execute('''CREATE TABLE IF NOT EXISTS api_jobs (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL, state TEXT NOT NULL,
                    cancel INTEGER NOT NULL, result TEXT, updated REAL NOT NULL)''')
                if "policy" not in {row["name"] for row in connection.execute("PRAGMA table_info(api_jobs)")}:
                    connection.execute("ALTER TABLE api_jobs ADD COLUMN policy TEXT")
                connection.execute("UPDATE api_jobs SET state='recovery_pending' WHERE state IN ('queued','running','cancel_requested')")
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="synthetic-jobs")
            self.executor.submit(self._recover)
        except BaseException:
            self.lease.__exit__(None, None, None)
            raise

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _recover(self):
        try:
            with JobJournal(self.root / "workers") as journal:
                results = recover(journal, DockerProbe(self.docker_exe))
                self.recovery_ok = all(item["cleanup_verified"] for item in results)
            if self.recovery_ok:
                with self.connect() as connection:
                    connection.execute("UPDATE api_jobs SET state='interrupted', result=? WHERE state='recovery_pending'",
                                       (json.dumps({"cleanup_verified": True}),))
        except Exception:
            self.recovery_ok = False
        finally:
            self.ready.set()

    def submit(self, profile_name):
        profile = selected_profile(profile_name)
        with self.lock:
            if self.closed or not self.ready.is_set() or not self.recovery_ok:
                raise ServiceUnavailable("Synthetic controller is not ready")
            job = uuid.uuid4().hex
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                count = connection.execute("SELECT COUNT(*) FROM api_jobs WHERE state IN ('queued','running','cancel_requested')").fetchone()[0]
                if count >= 4:
                    raise QueueFull("Synthetic queue is full")
                connection.execute("INSERT INTO api_jobs (id, profile, state, cancel, result, updated, policy) VALUES (?, ?, 'queued', 0, NULL, ?, ?)",
                                   (job, profile.name, time.time(), json.dumps(policy_snapshot(profile.name))))
            try:
                self.executor.submit(self._execute, job)
            except Exception:
                with self.connect() as connection:
                    connection.execute("UPDATE api_jobs SET state='service_error' WHERE id=?", (job,))
                raise ServiceUnavailable("Synthetic controller could not queue the job") from None
            return self.status(job)

    def _cancelled(self, job):
        with self.connect() as connection:
            row = connection.execute("SELECT cancel FROM api_jobs WHERE id=?", (job,)).fetchone()
        return self.closed or row is None or bool(row["cancel"])

    def _execute(self, job):
        result = {"status": "service_error", "cleanup_verified": False}
        claimed = False
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT profile, state, policy FROM api_jobs WHERE id=?", (job,)).fetchone()
                if row is None or row["state"] != "queued":
                    return
                connection.execute("UPDATE api_jobs SET state='running', updated=? WHERE id=?", (time.time(), job))
                claimed = True
            profile = selected_profile(row["profile"])
            if not row["policy"] or json.loads(row["policy"]) != policy_snapshot(profile.name):
                result = {"status": "policy_changed", "cleanup_verified": True}
                return
            with JobJournal(self.root / "workers") as journal:
                recovery = recover(journal, DockerProbe(self.docker_exe))
                if any(not item["cleanup_verified"] for item in recovery):
                    raise ServiceUnavailable("Worker recovery is incomplete")
                result = run_managed_probe(journal, DockerProbe(self.docker_exe), profile.kind,
                                           cancelled=lambda: self._cancelled(job), timeout_seconds=profile.wall_seconds)
        except Exception:
            pass
        finally:
            # A queued cancellation returns before a worker exists and remains terminal.
            if claimed:
                safe = {key: result[key] for key in ("status", "cleanup_verified", "synthetic_passed", "output_bytes", "capture_bytes") if key in result}
                with self.connect() as connection:
                    connection.execute("UPDATE api_jobs SET state=?, result=?, updated=? WHERE id=?",
                                       (result["status"], json.dumps(safe), time.time(), job))
                if not result.get("cleanup_verified"):
                    self.recovery_ok = False

    def status(self, job):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM api_jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise KeyError(job)
        result = json.loads(row["result"]) if row["result"] else {}
        policy = json.loads(row["policy"]) if row["policy"] else {}
        return {"job_id": row["id"], "profile": row["profile"], "status": row["state"],
                "policy_id": policy.get("policy_id"),
                "cancel_requested": bool(row["cancel"]), "cleanup_verified": result.get("cleanup_verified"),
                "synthetic_passed": result.get("synthetic_passed"), "tested": False,
                "repository_execution_enabled": False}

    def cancel(self, job):
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM api_jobs WHERE id=?", (job,)).fetchone()
            if row is None:
                raise KeyError(job)
            if row["state"] == "queued":
                connection.execute("UPDATE api_jobs SET cancel=1, state='cancelled', result=?, updated=? WHERE id=?",
                                   (json.dumps({"cleanup_verified": True}), time.time(), job))
            elif row["state"] in ("running", "cancel_requested"):
                connection.execute("UPDATE api_jobs SET cancel=1, state='cancel_requested', updated=? WHERE id=?", (time.time(), job))
        return self.status(job)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            with self.connect() as connection:
                connection.execute("UPDATE api_jobs SET cancel=1, state='cancelled', result=? WHERE state='queued'",
                                   (json.dumps({"cleanup_verified": True}),))
                connection.execute("UPDATE api_jobs SET cancel=1 WHERE state IN ('running','cancel_requested')")
        self.executor.shutdown(wait=True)
        self.lease.__exit__(None, None, None)
