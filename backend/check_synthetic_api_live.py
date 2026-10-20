"""In-process API check with real, fixed trusted Docker fixtures only."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from synthetic_jobs_api import router, close_synthetic_service


def app_instance():
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await close_synthetic_service(app)
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app


def live_check(root, docker_exe):
    # This credential is process-local, never printed or written to a file.
    token = secrets.token_urlsafe(32)
    headers = {"Authorization": "Bearer " + token}
    environment = {
        "CODEPILOT_SYNTHETIC_API_ENABLED": "1", "CODEPILOT_SYNTHETIC_API_TOKEN": token,
        "CODEPILOT_SYNTHETIC_RUNTIME": str(Path(root).absolute()),
        "CODEPILOT_SYNTHETIC_DOCKER": str(Path(docker_exe).absolute()),
    }

    def submit(client, profile):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = client.post("/synthetic-jobs", headers=headers, json={"profile": profile})
            if response.status_code == 202:
                return response.json()["job_id"]
            if response.status_code != 503:
                raise AssertionError("Unexpected job creation status")
            time.sleep(0.1)
        raise AssertionError("Synthetic service did not become ready")

    def finished(client, job):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            response = client.get("/synthetic-jobs/" + job, headers=headers)
            assert response.status_code == 200
            body = response.json()
            if body["status"] not in {"queued", "running", "cancel_requested", "recovery_pending"}:
                return body
            time.sleep(0.05)
        raise AssertionError("Controlled API job did not finish")

    with patch.dict(os.environ, environment):
        with TestClient(app_instance()) as client:
            assert client.get("/synthetic-jobs/profiles").status_code == 401
            assert client.post("/synthetic-jobs", headers=headers,
                               json={"profile": "arithmetic-patched-v1", "command": "not accepted"}).status_code == 422
            baseline_id = submit(client, "arithmetic-baseline-v1")
            baseline = finished(client, baseline_id)
            patched_id = submit(client, "arithmetic-patched-v1")
            patched = finished(client, patched_id)
            sleeper_id = submit(client, "cancellation-demo-v1")
            # Let the known sleeper actually start before requesting cancellation.
            time.sleep(2)
            cancellation_response = client.post("/synthetic-jobs/" + sleeper_id + "/cancel", headers=headers)
            assert cancellation_response.status_code == 202
            cancelled = finished(client, sleeper_id)
        with TestClient(app_instance()) as restarted:
            response = restarted.get("/synthetic-jobs/" + patched_id, headers=headers)
            assert response.status_code == 200
            restored = response.json()
    checks = {"baseline": baseline, "patched": patched, "cancelled": cancelled, "restored": restored}
    passed = (baseline["status"] == patched["status"] == "completed"
              and baseline["synthetic_passed"] is False and patched["synthetic_passed"] is True
              and cancelled["status"] == "cancelled" and restored == patched
              and all(row["cleanup_verified"] is True and row["tested"] is False for row in checks.values()))
    return {"status": "live_synthetic_api_passed" if passed else "check_failed", "jobs": checks,
            "unauthenticated_rejected": True, "extra_fields_rejected": True,
            "repository_execution_enabled": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = live_check(args.root, args.docker)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "live_synthetic_api_passed" else 1)
