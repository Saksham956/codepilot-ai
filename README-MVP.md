# CodePilot AI — local MVP

Run `powershell -ExecutionPolicy Bypass -File .\Start-CodePilot.ps1` from this project folder, then open http://127.0.0.1:8010. Keep the terminal open; Ctrl+C stops the app. The existing backend virtual environment is used.

## First use

1. Choose the verified Requests example and open its report. No key, network access or Docker is needed to read the saved evidence. This is explicitly a recorded result, not a fresh test run.
2. Download the JSON report. It contains the pinned revision, source artifact and worker identity, baseline observations and the no-compatible-patch decision.
3. For a live investigation, choose the GitHub workflow, enter a public Python repository and an issue, and confirm sending selected code to Gemini. Review the result, its risks and validation scope.
4. A validated proposal provides a unified diff download. Apply it manually only after review. The app never changes the original repository. Rejected proposals provide no diff. No-change and needs-more-context are valid outcomes.

## Fresh installation on Windows

Install Python 3.13 and Git. From this project folder:

```powershell
py -3.13 -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements-mvp.txt
```

These are exact direct dependency versions verified in the existing environment. Transitive dependencies are resolved by pip; this is not a fully hash-locked backend environment. The Docker test-worker lock is separate. Do not replace an existing environment without preserving anything you need.

For live AI, create backend/.env with GEMINI_API_KEY set to your own key. Never commit it. Gemini usage may incur provider charges. The recorded example works without that configuration. Network access is needed for GitHub and Gemini in live mode. No real provider call was required for the app's automated verification; its live workflow was tested with a controlled provider response.

## What is complete

- Local responsive web interface with loading/error states, explicit source-sharing consent, reports, evidence and downloads.
- Live public-GitHub source selection and structured AI proposals, including no-change results.
- Full-file multi-edit validation and disposable-copy application; unified diff export.
- Recorded real-source Requests evidence and compatibility decision integrated into the report flow.
- Focused workflow tests covering success, rejection, no change, original-file preservation, request boundaries and sanitized errors.

## Execution scope

Live AI proposals receive exact-match and Python syntax validation only. They are not behavior-tested. The recorded Requests evidence came from the separately implemented pinned isolated runner. Arbitrary repositories, setup scripts and tests are not executed through this web app. The generic repository executor remains disabled. Therefore this local MVP is not a complete autonomous repair-and-test platform or a public production service.

The existing main.py teaching API and synthetic-job API remain available as separate entry points. Start-CodePilot.ps1 starts mvp_app.py, the unified local user flow. Do not expose its port to other machines; it has no user-account authentication or durable background job queue. One live investigation runs at a time, with Git's existing clone timeout. AI response time depends on the provider.

## Learning checkpoints

- UI: forms collect intent; request state keeps loading, success and failure clear.
- Backend: one report schema makes AI proposals and evidence-backed no-change outcomes usable in the same flow.
- Validation: syntax success does not establish correct behavior.
- Downloads: exporting a diff lets a person review a proposed change before applying it.
- Testing: controlled provider responses verify the workflow without spending API credits or claiming a real provider integration test.

## Remaining work beyond this MVP

General isolated baseline/patched test execution, reviewed profiles for other repositories, durable job history/cancellation in the unified workflow, provider integration checks with configured credentials, deployment authentication and operational monitoring. Full original phases were never specified in a roadmap, so this document does not claim those unknown phases are complete.

## Fresh approved baseline run
Choose Run fresh Requests baseline in Docker in the workflow selector. Docker Desktop's Linux engine and the already-built pinned worker image must be available. This invokes only the fixed Requests source artifact and 19 checks; it does not accept custom source, commands or images. The browser displays and exports the fresh result. See MVP-VERIFICATION.md for the regression scope.


## Approved upstream comparison
The ItsDangerous learning comparison workflow is now available. It runs eight selected upstream cases on unchanged source twice, on a deliberately broken temporary copy, and on its validated restoration. Reports and repair diffs are downloadable and saved in job history. One separately authorized real Gemini request also produced the expected repair, which passed the eight selected cases. This supersedes the earlier statement that no provider request had been made. The example remains explicitly seeded and is not evidence of autonomous bug discovery.


## Final local portfolio delivery
See LOCAL-MVP-COMPLETION.md for the final scope and verification, PROJECT-DELIVERY.md for the complete walkthrough, and Verify-CodePilot.ps1 for repeatable acceptance checks. requirements-mvp-lock.txt records the fresh Windows/Python 3.13 acceptance environment; it can be used instead of requirements-mvp.txt to retain those exact transitive versions. Recorded Gemini evidence is available in the workflow selector without any new provider request.

