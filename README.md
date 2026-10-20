# CodePilot AI

A local portfolio MVP for AI-assisted Python repository investigation, safe patch validation and selected Docker-isolated test workflows.

## Features

- FastAPI backend and responsive browser interface.
- Gemini-assisted proposals with explicit source-sharing consent.
- Full-file, multi-edit syntax validation and temporary-copy application.
- Unified diff and JSON report downloads.
- SQLite-backed background jobs, history and cooperative cancellation.
- Reviewed Requests characterization and ItsDangerous upstream-test comparison profiles.
- Durable Docker ownership records and crash-recovery verification.

## Quick start — Windows / Python 3.13

```powershell
py -3.13 -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements-mvp-lock.txt
powershell -ExecutionPolicy Bypass -File .\Start-CodePilot.ps1
```

Open http://127.0.0.1:8010. The recorded evidence workflows work without an API key or Docker. For live Gemini proposals, create `backend/.env` with your own `GEMINI_API_KEY`. Never commit that file. Provider usage can consume quota or incur charges.

Fresh Docker workflows also require Docker Desktop's Linux engine and the approved immutable worker image. That local image and downloaded wheel archives are not included in Git. The backend's offline-worker guides describe the pinned dependency inputs and image recipe. Saved evidence remains readable without rebuilding them.

## Verification

```powershell
powershell -ExecutionPolicy Bypass -File .\Verify-CodePilot.ps1
```

The recorded local acceptance run contains 101 tests: 100 passed and one Windows symlink-privilege test was skipped. Separate approved execution evidence shows eight upstream ItsDangerous cases passing, two failures after a deliberate temporary mutation, and eight passes after restoration. One authorized Gemini request also returned the expected restoration, which passed the selected cases.

The expected repair was supplied to Gemini. This is an explicitly seeded learning exercise, not autonomous discovery of an upstream bug. The complete upstream suites were not run.

## Scope

This is a single-user, loopback-only portfolio MVP. Arbitrary repository execution is disabled; unreviewed live proposals receive syntax validation rather than behavior tests. Cancellation is cooperative. No public deployment authentication is provided.

Read [the delivery guide](PROJECT-DELIVERY.md), [completion scope](LOCAL-MVP-COMPLETION.md), and [setup notes](README-MVP.md) for details and interview checkpoints.

## Source notices

Pinned upstream source/test snapshots and source artifacts are included for reproducible reviewed demonstrations. Their original license notices are in each input directory; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Credentials, environments, job databases, backups and downloaded wheel archives are excluded.
