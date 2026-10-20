from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from execution_runner import run_patch_job
from synthetic_execution_policy import PROFILES, selected_profile
from synthetic_job_service import SyntheticJobService, QueueFull, ServiceUnavailable
from synthetic_jobs_api import router, close_synthetic_service


TOKEN = "synthetic-test-only-" + "x" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}
PROFILE = "arithmetic-patched-v1"


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("Controlled check timed out")


def make_app():
    @asynccontextmanager
    async def lifespan(app):
        yield
        await close_synthetic_service(app)
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app


class ApiBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "CODEPILOT_SYNTHETIC_API_ENABLED": "1", "CODEPILOT_SYNTHETIC_API_TOKEN": TOKEN,
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = make_app()
        self.fake = Mock()
        self.fake.status.return_value = {"job_id": "a" * 32, "tested": False, "repository_execution_enabled": False}
        self.fake.submit.return_value = self.fake.status.return_value
        self.fake.cancel.return_value = self.fake.status.return_value
        self.app.state.synthetic_job_service = self.fake
        self.client = TestClient(self.app)

    def test_disabled_and_unauthenticated_calls_never_touch_service(self):
        with patch.dict(os.environ, {"CODEPILOT_SYNTHETIC_API_ENABLED": "0"}):
            self.assertEqual(self.client.get("/synthetic-jobs/profiles", headers=HEADERS).status_code, 503)
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            self.assertEqual(self.client.post("/synthetic-jobs", json={"profile": PROFILE}, headers=headers).status_code, 401)
        self.fake.submit.assert_not_called()

    def test_short_operator_token_rejected(self):
        with patch.dict(os.environ, {"CODEPILOT_SYNTHETIC_API_TOKEN": "short"}):
            self.assertEqual(self.client.get("/synthetic-jobs/profiles", headers=HEADERS).status_code, 503)

    def test_profiles_only_allow_backend_selections(self):
        response = self.client.get("/synthetic-jobs/profiles", headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["profile"] for row in response.json()["profiles"]}, set(PROFILES))
        self.assertFalse(any(row.kind in {"orphan", "flood"} for row in PROFILES.values()))
        with self.assertRaises(ValueError):
            selected_profile("../../command")

    def test_arbitrary_execution_fields_rejected(self):
        for extra in ({"command": "python evil.py"}, {"repo_path": "D:/private"}, {"image": "other"}, {"tests": ["evil.py"]}):
            response = self.client.post("/synthetic-jobs", headers=HEADERS, json={"profile": PROFILE, **extra})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post("/synthetic-jobs", headers=HEADERS, json={"profile": "unknown"}).status_code, 422)
        self.fake.submit.assert_not_called()

    def test_creation_status_and_cancel(self):
        self.assertEqual(self.client.post("/synthetic-jobs", headers=HEADERS, json={"profile": PROFILE}).status_code, 202)
        self.assertEqual(self.client.get("/synthetic-jobs/" + "a" * 32, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.post("/synthetic-jobs/" + "a" * 32 + "/cancel", headers=HEADERS).status_code, 202)
        self.fake.submit.assert_called_once_with(PROFILE)

    def test_unknown_and_malformed_job_ids(self):
        self.fake.status.side_effect = KeyError()
        self.fake.cancel.side_effect = KeyError()
        self.assertEqual(self.client.get("/synthetic-jobs/" + "b" * 32, headers=HEADERS).status_code, 404)
        self.assertEqual(self.client.post("/synthetic-jobs/" + "b" * 32 + "/cancel", headers=HEADERS).status_code, 404)
        self.assertEqual(self.client.get("/synthetic-jobs/not-a-job", headers=HEADERS).status_code, 422)

    def test_queue_full_and_not_ready(self):
        for error, code in ((QueueFull(), 429), (ServiceUnavailable(), 503)):
            self.fake.submit.side_effect = error
            self.assertEqual(self.client.post("/synthetic-jobs", headers=HEADERS, json={"profile": PROFILE}).status_code, code)

    def test_missing_runtime_does_not_create_service(self):
        del self.app.state.synthetic_job_service
        with patch.dict(os.environ, {"CODEPILOT_SYNTHETIC_RUNTIME": "", "CODEPILOT_SYNTHETIC_DOCKER": ""}), patch("synthetic_jobs_api.SyntheticJobService") as create:
            response = self.client.post("/synthetic-jobs", headers=HEADERS, json={"profile": PROFILE})
        self.assertEqual(response.status_code, 503)
        create.assert_not_called()

    def test_production_execution_remains_disabled(self):
        self.assertEqual(run_patch_job(None, None, None)["execution_status"], "execution_disabled")


class MainIntegrationTests(unittest.TestCase):
    def test_existing_app_mounts_protected_router(self):
        import importlib.util
        import sys
        from types import ModuleType
        directory = Path(__file__).resolve().parent
        source = directory / "main_with_synthetic_api.py"
        if not source.exists():
            source = directory / "main.py"
        modules = {}
        for name, functions in {
            "ai_service": ["ask_ai", "detect_bugs_with_ai", "generate_patch_with_ai"],
            "analyzer": ["get_bug_relevant_code", "get_relevant_code", "analyze_repository"],
            "github_service": ["clone_repository"],
        }.items():
            module = ModuleType(name)
            for function in functions:
                setattr(module, function, Mock(side_effect=AssertionError("External service forbidden")))
            modules[name] = module
        spec = importlib.util.spec_from_file_location("main_api_wiring_check", source)
        main = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {"CODEPILOT_SYNTHETIC_API_ENABLED": "0"}):
            spec.loader.exec_module(main)
            with TestClient(main.app) as client:
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/synthetic-jobs/profiles").status_code, 503)
        self.assertIn("/generate-patch", main.app.openapi()["paths"])
        self.assertIn("/synthetic-jobs", main.app.openapi()["paths"])


class PersistentServiceTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        self.root = Path(self.owner.name) / "runtime"
        self.recovery = patch("synthetic_job_service.recover", return_value=[])
        self.recovery.start()
        self.addCleanup(self.recovery.stop)
        self.service = SyntheticJobService(self.root, "docker")
        self.addCleanup(self.service.close)
        self.assertTrue(self.service.ready.wait(5))

    def test_result_persists_across_restart(self):
        with patch("synthetic_job_service.run_managed_probe", return_value={"status": "completed", "cleanup_verified": True, "synthetic_passed": True, "raw_output": "do not persist"}):
            job = self.service.submit(PROFILE)["job_id"]
            wait_until(lambda: self.service.status(job)["status"] == "completed")
        self.service.close()
        replacement = SyntheticJobService(self.root, "docker")
        self.addCleanup(replacement.close)
        self.assertTrue(replacement.ready.wait(5))
        self.assertTrue(replacement.status(job)["synthetic_passed"])
        with replacement.connect() as connection:
            row = connection.execute("SELECT result FROM api_jobs WHERE id=?", (job,)).fetchone()
        self.assertNotIn("raw_output", row[0])

    def slow_worker(self, *args, cancelled, **kwargs):
        self.started.set()
        wait_until(cancelled, seconds=5)
        return {"status": "cancelled", "cleanup_verified": True}

    def test_running_and_queued_cancellation(self):
        self.started = threading.Event()
        with patch("synthetic_job_service.run_managed_probe", side_effect=self.slow_worker) as worker:
            first = self.service.submit("cancellation-demo-v1")["job_id"]
            self.assertTrue(self.started.wait(5))
            second = self.service.submit(PROFILE)["job_id"]
            self.assertEqual(self.service.cancel(second)["status"], "cancelled")
            self.service.cancel(first)
            wait_until(lambda: self.service.status(first)["status"] == "cancelled")
            self.service.executor.shutdown(wait=True)
            self.assertEqual(worker.call_count, 1)

    def test_queue_cap_is_enforced(self):
        self.started = threading.Event()
        with patch("synthetic_job_service.run_managed_probe", side_effect=self.slow_worker):
            self.service.submit("cancellation-demo-v1")
            self.assertTrue(self.started.wait(5))
            for _ in range(3):
                self.service.submit(PROFILE)
            with self.assertRaises(QueueFull):
                self.service.submit(PROFILE)
            self.service.close()

    def test_shutdown_requests_active_cleanup(self):
        self.started = threading.Event()
        with patch("synthetic_job_service.run_managed_probe", side_effect=self.slow_worker):
            job = self.service.submit("cancellation-demo-v1")["job_id"]
            self.assertTrue(self.started.wait(5))
            self.service.close()
        self.assertEqual(self.service.status(job)["status"], "cancelled")

    def test_second_service_refused(self):
        with self.assertRaises(RuntimeError):
            SyntheticJobService(self.root, "docker")

    def test_unfinished_jobs_marked_interrupted_after_recovery(self):
        self.service.close()
        with self.service.connect() as connection:
            connection.execute("INSERT INTO api_jobs (id, profile, state, cancel, result, updated) VALUES (?, ?, 'running', 0, NULL, 0)", ("c" * 32, PROFILE))
        replacement = SyntheticJobService(self.root, "docker")
        self.addCleanup(replacement.close)
        self.assertTrue(replacement.ready.wait(5))
        self.assertEqual(replacement.status("c" * 32)["status"], "interrupted")
        self.assertTrue(replacement.status("c" * 32)["cleanup_verified"])

    def test_cleanup_error_blocks_new_work(self):
        with patch("synthetic_job_service.run_managed_probe", return_value={"status": "cleanup_error", "cleanup_verified": False}):
            job = self.service.submit(PROFILE)["job_id"]
            wait_until(lambda: not self.service.recovery_ok)
        self.assertEqual(self.service.status(job)["status"], "cleanup_error")
        with self.assertRaises(ServiceUnavailable):
            self.service.submit(PROFILE)

    def test_policy_change_blocks_execution(self):
        from synthetic_execution_policy import policy_snapshot
        import json
        job = "d" * 32
        changed = policy_snapshot(PROFILE)
        changed["settings"]["wall_seconds"] = 999
        with self.service.connect() as connection:
            connection.execute("INSERT INTO api_jobs (id, profile, state, cancel, updated, policy) VALUES (?, ?, 'queued', 0, 0, ?)", (job, PROFILE, json.dumps(changed)))
        with patch("synthetic_job_service.run_managed_probe") as worker:
            self.service._execute(job)
        worker.assert_not_called()
        self.assertEqual(self.service.status(job)["status"], "policy_changed")


if __name__ == "__main__":
    unittest.main()
