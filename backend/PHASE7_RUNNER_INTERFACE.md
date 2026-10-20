# Phase 7: disabled runner interface and fake-worker lifecycle

Implemented October 20, 2026. This is the first coding task from the Step 2 design. No real test executor or public execution endpoint has been added.

## Step by step

1. `execution_runner.py` provides `run_patch_job(repo_path, changes, allowed_files)`. It immediately returns `execution_disabled`, `tested: false`, and `comparison: not_run`. It does not read the repository, create copies, or start workers. There is deliberately no setting or `enabled=True` parameter that bypasses this boundary.
2. A separate developer-only function, `simulate_patch_job`, exercises job ownership with a trusted fake-worker factory. It is not connected to FastAPI or user request parameters. The factory must provide a context manager whose worker returns a structured `RunResult`.
3. Simulation first creates its own baseline snapshot, excluding `.git`. The original caller-owned directory remains unchanged. The existing full-file validator then creates the patched snapshot. Invalid batches stop before any fake worker starts.
4. Simulation opens a fresh fake-worker context for each snapshot. Both receive the same immutable execution profile. Profile limits are metadata for the future worker adapter; no resource enforcement is claimed here.
5. Completed fake results can describe failure-to-pass, possible regression, no selected regression, or failures remaining. Every comparison starts with `simulated_`. Even when both fake results are completed, `tested` remains false.
6. Incomplete fake results distinguish timeout, resource limit, output limit, unavailable dependency, collection error, and worker error. They stop the simulation and leave the comparison as `not_run`.
7. A cancellation callback is checked before copying, before each worker, and after each worker. Context managers close workers and delete owned copies on ordinary failure or an interruption. The caller's source directory is retained.

## Honest limits

This is synchronous lifecycle scaffolding, not a security sandbox. Cooperative cancellation cannot interrupt an actively blocked worker. Timeout and resource states are fake results, not measured or enforced limits. There is no process launcher, dependency installer, queue, persistence, service-restart recovery, or real isolation adapter.

Worker cleanup depends on its context manager behaving correctly; an eventual real adapter must terminate and reap the whole isolated environment. Cleanup or copy errors return a generic simulation error rather than exposing host paths. Cleanup errors still require backend operational handling in a future persistent job service.

Fake-worker factories are trusted developer code; they must never come from repository contents, AI output, or public request fields. This module does not make an arbitrary supplied callback safe.

`/generate-patch` keeps its existing static proposal behavior. No API route or backend AI service was changed.

## Interview checkpoints

- **What is a protocol?** An interface describing operations a worker must provide, allowing the job flow to be checked with a fake implementation.
- **What does fail closed mean here?** The production entry point refuses execution when a verified isolated worker is unavailable. It has no local fallback.
- **Why separate production and simulation entry points?** A fake result must never be reported as an actual repository test result or accidentally enable execution.
- **Why use an ExitStack?** It owns multiple resources whose creation happens at different times, and releases them in reverse order on every exit path.
- **Why use two fresh workers?** The patched run should not inherit files, memory, or side effects left by the baseline run.
- **Does a timeout result enforce a timeout?** No. A real external worker controller must enforce it; this phase checks only how the report and cleanup flow handle that outcome.
- **Does `simulated_failure_to_pass` prove the bug was fixed?** No. It verifies comparison logic with scripted outcomes only.

## Verification

`test_runner_lifecycle.py` checks disabled execution before source access, separate fake workers, equal profiles, all four completed comparison combinations, invalid validation, incomplete outcomes, worker exceptions, partial results, cancellation, interruption cleanup, malformed worker returns, and invalid result states. Fixtures contain an executable `raise` statement but are copied, read, and parsed only; importing them would fail the checks.

Run only this trusted suite when checking this phase. Do not run automatic discovery against downloaded repositories.

Next: select and configure a real isolation boundary, then verify it exclusively with trusted synthetic fixtures before allowing repository execution. That selection and implementation are not included in this change.
