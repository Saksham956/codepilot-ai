# Phase 7: report jobs and local history

The unified interface now submits background jobs instead of holding the page request open. One worker processes investigations in order. Four queued/active jobs are allowed; history is capped at 1,000 records and the interface shows the latest 50.

## How it works

1. POST /api/jobs saves an identity and queued state in a local SQLite database, then schedules the investigation. The issue and submission payload are kept in memory for pending work rather than persisted.
2. GET /api/jobs lists recent metadata. GET /api/jobs/{id} returns progress and the completed report.
3. The browser polls while a job is queued, running or cancelling. It remembers the last job identity, so a refresh can reopen the report or resume observing active work.
4. POST /api/jobs/{id}/cancel cancels queued work before execution. Active cancellation waits for the current investigation operation and cleanup; the resulting report is discarded. This is not immediate process termination.
5. Completed reports survive application restart. Unfinished work is marked interrupted rather than replayed. A process-level file lease rejects a second controller for the same history database.
6. Graceful shutdown requests cancellation, waits for active work to finish and releases the history lease. Forced termination during a Docker operation can still require manual container inspection; this job layer does not add a durable Docker resource journal to the fixed real-source runner.

Report history is stored under backend/local_report_jobs/reports.sqlite3. Reports can contain source snippets, issues and diffs; treat this local database as project data. It is not an encrypted multi-user store. The application must remain bound to loopback.

## Verification

Thirteen selected tests passed: nine existing MVP report-flow tests plus four new service tests. New checks cover durable results after restart, cancelling queued/active work, interrupted-state recovery and sanitized errors. No new repository code or provider requests were needed for these checks.

## Interview checkpoints

- Why use a background job? The HTTP request can finish quickly while work continues independently of the page.
- Why persist state? A refreshed browser or restarted app should not pretend earlier work never existed.
- Why distinguish cancelling from cancelled? A cancellation request does not mean an active operation has already stopped.
- Why mark interrupted rather than retry automatically? Replaying work may repeat external API requests and costs.
- Why is a thread not a sandbox? It organizes trusted orchestration; real repository code still requires the isolated worker.
