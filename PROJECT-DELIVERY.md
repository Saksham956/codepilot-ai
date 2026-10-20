# CodePilot AI — local portfolio MVP delivery

## Delivery scope

This delivery is the documented local portfolio MVP selected by the user. It combines a local interface, AI patch proposals, safe full-file patch validation, selected real-source tests in Docker, job history, report downloads and two reviewed demonstration profiles. It is not a general autonomous service for arbitrary repositories.

## Run it

From D:\AI-Projects\codepilot-ai:

```powershell
powershell -ExecutionPolicy Bypass -File .\Start-CodePilot.ps1
```

Open http://127.0.0.1:8010. Keep the terminal open. Ctrl+C stops the server; graceful shutdown waits for an active investigation to finish its current operation.

For a fresh backend environment, use Python 3.13 and Git:

```powershell
py -3.13 -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements-mvp.txt
```

The direct dependency file is version-pinned, not a complete cross-platform hash lock. Fresh Docker runs additionally require Docker Desktop's Linux engine, the approved immutable worker image and bundled source artifacts. An absent image causes a failure; the app never substitutes an arbitrary image or installs repository dependencies during test execution.

## Five-minute demo

1. Open the verified Requests example. Show the recorded source identities and explain why a no-compatible-patch decision is useful.
2. Choose the ItsDangerous learning comparison. The job runs in the background: unchanged source passes eight cases twice, the deliberately broken copy fails two, and the repair passes eight. Open the repair diff and download the JSON report.
3. Refresh the page. The completed report and recent history remain available.
4. Choose the verified Gemini repair evidence. Explain that this is a previously authorized live provider request, not a new paid request. The expected repair was supplied to the model; its candidate matched approved bytes and passed eight cases.
5. Optionally use live GitHub analysis with your own key and explicit source-sharing consent. For arbitrary repositories, proposals are syntax-checked, not behavior-tested.

## Evidence map

| Capability | Evidence |
|---|---|
| Requests compatibility behavior | backend/real_auth_matrix_results.json |
| Selected upstream pytest comparison | backend/itsdangerous_comparison_results.json |
| Real Gemini integration | backend/gemini_learning_repair_results.json |
| Controller-crash container recovery | backend/real_worker_recovery_results.json |
| Trusted acceptance tests | backend/local_mvp_acceptance_results.json |

## Architecture for interviews

Browser form → validated FastAPI request → SQLite-backed job queue → source selection / approved profile → AI proposal or fixed exercise → full-file validator → isolated selected tests when a profile permits → scoped report and diff.

The model proposes text. Trusted backend code controls file access, validates the entire edit batch, applies to a disposable copy and owns process execution. A valid Python syntax tree is not proof of correct behavior. A Docker resource journal records intent before creation; cleanup verifies labels and immutable IDs. Job state recovery and container resource recovery solve different problems.

## Honest resume wording

Built a local AI-assisted Python code-review and patch-validation platform with FastAPI, Gemini, Docker-isolated tests for reviewed profiles, SQLite job history, unified diff export and crash-recovery checks. Demonstrated baseline/failure/repair comparisons using selected upstream tests and a clearly labelled seeded defect.

Do not claim autonomous discovery of an upstream bug, full-suite coverage, unrestricted safe execution, or production multi-user deployment.

## Limits and future roadmap

- Generic repository execution stays disabled. Only reviewed source/test profiles run in Docker.
- Cancellation is cooperative; it does not interrupt an in-flight provider operation immediately.
- Docker crash recovery runs before the next real-worker invocation, not at startup. Host patch directories are not covered by the Docker journal after a forced process kill.
- Local report history can contain issues, source and diffs. It is not encrypted or user-account isolated.
- The server is loopback-only and has no deployment authentication. Do not publish it as a multi-user service.
- Full fresh-machine validation, general AI-candidate test execution, public deployment and additional repository profiles are future work beyond this local delivery.

## Learning checkpoints

1. Why is the complete file used for validation when only selected snippets go to the model?
2. Why can a test pass while reproducing UnicodeEncodeError?
3. What distinguishes generated, syntax-valid, temporarily applied and behavior-tested code?
4. Why must a container's name alone not authorize deletion?
5. Why is the seeded exercise different from discovering a real upstream defect?

The implementation guides in backend/PHASE7_*.md develop these topics step by step. Later guides supersede the historical status sections in earlier handoffs.
