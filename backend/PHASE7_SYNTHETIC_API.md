# Phase 7: protected synthetic job API

Completed October 20, 2026. These endpoints control only fixed backend-authored demonstration programs. Repository execution remains disabled. The API is opt-in and disabled in an ordinary backend session unless explicitly configured.

## What changed

1. `synthetic_execution_policy.py` defines three approved profiles: `arithmetic-baseline-v1`, `arithmetic-patched-v1`, and `cancellation-demo-v1`. Requests cannot provide source files, repositories, commands, images, arbitrary test names, or resource-limit overrides. The flooding and crash fixtures from developer checks are not public profiles.
2. Each queued job records a policy snapshot and fingerprint, including its image, fixture identity where applicable, timeout, and resource settings. Before execution, the service compares that record with the current policy. A mismatch produces `policy_changed` without executing the job.
3. `synthetic_job_service.py` persists API job state in SQLite and runs one worker job at a time. At most four jobs may be queued or active. An operating-system lease refuses a second service process sharing the same runtime directory.
4. A service restart recovers recorded workers before accepting new work. Old unfinished requests become `interrupted` after successful recovery; they are not silently replayed. Completed status remains readable. Cleanup failure blocks new submissions until it is resolved and the service recovers.
5. Cancellation is durable. Queued work can be cancelled before a worker exists. Running work receives a cancellation request through the controller's live polling. A request arriving after completion does not undo a completed result. Shutdown requests cancellation and waits for worker cleanup.
6. `synthetic_jobs_api.py` authenticates every route with an operator-configured bearer token. Missing opt-in or credentials produces 503; missing or incorrect authentication produces 401. Authentication happens before service creation. Only these new routes are protected by this token; existing analysis routes retain their prior behavior.
7. `main.py` mounts the router and adds shutdown cleanup through FastAPI's lifespan. Existing `/generate-patch` behavior and the disabled production execution entry point are unchanged.

## Endpoints

All requests require `Authorization: Bearer <operator token>`.

| Method and path | Purpose |
| --- | --- |
| `GET /synthetic-jobs/profiles` | List the three fixed profiles. Does not initialize a worker service. |
| `POST /synthetic-jobs` | Submit `{"profile":"arithmetic-patched-v1"}`; returns 202 and a job ID. Extra fields are rejected. |
| `GET /synthetic-jobs/{job_id}` | Read persisted status, policy fingerprint, cancellation flag, and synthetic outcome. |
| `POST /synthetic-jobs/{job_id}/cancel` | Request cancellation; returns 202 with current status. Repeat requests are safe. |

A 202 response means the request was accepted, not that tests passed or cancellation finished. Poll the status endpoint until the job reaches a terminal state. `completed` describes completion of a trusted synthetic program; check `synthetic_passed` separately for the arithmetic outcome. `tested` and `repository_execution_enabled` remain false.

Unknown job IDs return 404; malformed IDs, unsupported profiles, and extra creation fields return 422. A full queue returns 429. Startup recovery or an unavailable controller returns 503.

## Easiest beginner walkthrough

The installed `check_synthetic_api_live.py` script exercises the API inside one process. It generates a temporary token without displaying it, creates real isolated workers for the fixed fixtures, polls results, cancels the sleeper, closes the service, recreates it, and checks restored status. No server port opens, no Gemini calls occur, and transferred code is limited to the exact trusted fixtures.

With Docker Desktop running, open PowerShell in the backend folder and run:

```powershell
$demoDocker = (Get-Command docker.exe).Source
& .\venv\Scripts\python.exe .\check_synthetic_api_live.py `
  --root "$PWD\.synthetic-api-demo" `
  --docker $demoDocker `
  --report "$PWD\synthetic-api-demo-results.json"
```

Read the JSON result. The expected overall status is `live_synthetic_api_passed`; baseline is false, patched is true, the sleeper is cancelled, and cleanup is verified. The runtime directory is intentionally retained for durable status and recovery records. Keep it while any worker cleanup is unresolved.

The previous controlled run's receipt is included as `synthetic_api_live_results.json`. It is evidence from that check, not a promise that Docker or the machine is currently available.

## Optional operator setup for the actual backend

No permanent token or environment settings were installed. Before starting the backend, configure these process environment variables yourself:

| Variable | Required value |
| --- | --- |
| `CODEPILOT_SYNTHETIC_API_ENABLED` | Exactly `1` to opt in. |
| `CODEPILOT_SYNTHETIC_API_TOKEN` | A securely generated random token, 32–256 ASCII characters. Keep it private. |
| `CODEPILOT_SYNTHETIC_RUNTIME` | An absolute path to a private, persistent runtime directory. |
| `CODEPILOT_SYNTHETIC_DOCKER` | The absolute path to the trusted Docker executable. |

Run a single backend server worker, bound to localhost for this local pilot. Do not expose it publicly. Initial submissions may receive 503 while recovery finishes. The image must already exist locally; the API does not download images or install dependencies. Your existing `main.py` startup still uses its existing Gemini configuration; the standalone walkthrough avoids that service entirely.

The live demonstration uses process-local configuration only and does not enable the API in an already-running backend. Restart an existing backend after installing the new `main.py` if you want it to load the new routes.

## Verification and boundaries

Eighteen trusted unit/integration checks cover authentication, disabled defaults, strict requests, route mounting, queue limits, policy changes, status persistence, queued/running cancellation, restart handling, shutdown, and refusal of a second controller. The final live check used three trusted Docker fixtures and verified cancellation, cleanup, policy fingerprints, and persisted status after service recreation.

This is a single-operator local pilot. The token identifies an operator, not separate users or per-user authorization scopes. Results and journal rows have no automatic retention policy yet. The queue is serialized in-process, not a distributed scheduler. Recovery depends on the retained journal, the same Docker engine, and a responsive daemon. There is no service installation or scheduled reaper. Old terminal cleanup-error reports retain their original outcome even if later recovery succeeds.

## Interview checkpoints

- **Why choose a profile instead of accepting a command?** It makes the executable workload backend-owned and reviewable.
- **Why record the policy when submitting?** A queued job should not silently gain different permissions or settings after code changes.
- **Why return 202?** Execution and cancellation happen asynchronously; the caller must read the final status.
- **Why can a job be completed but fail its check?** Completion means the controlled program finished. Its assertion outcome is a different fact.
- **Why mark interrupted jobs rather than replay them?** Replaying after a crash can repeat side effects. Cleanup and a new explicit submission are clearer.
- **Why authenticate status endpoints too?** Even job IDs and operational results should be limited to the intended operator.

Next: define and review the repository test-selection and dependency policy. Enabling real repository execution is a separate later decision, not a consequence of enabling these synthetic endpoints.
