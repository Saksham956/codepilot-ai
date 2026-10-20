# Phase 7: durable ownership, recovery, cancellation, and bounded capture

Implemented October 20, 2026. This is a serialized developer-only controller for fixed trusted Docker probes. It is not connected to the production runner or FastAPI. Repository execution remains disabled.

## Step by step

1. `JobJournal` stores job status and container ownership in SQLite. An operating-system file lock permits one controller per journal directory. A crashed process releases that lock; a second live controller cannot acquire it. Database transactions commit with `synchronous=FULL` before container creation begins.
2. Each resource is recorded as planned before requesting Docker creation. The container receives unique job and journal labels, linking it to that record. Its name is randomly generated; clients cannot supply names, commands, scripts, images, or repository paths.
3. The controller uses the previously checked pinned image and isolation settings. It inspects the configuration before launch. The baseline and patched programs are the same exact backend-authored arithmetic fixtures used in the preceding step. Other probes are fixed sleeping or output-generating programs.
4. Input is fed and combined output is read on separate threads, allowing the controller to check cancellation and timeouts while the worker is active. It retains at most 64 KiB of output in memory. Above a 1 MiB read threshold it stops reading, closes the output pipe, and removes the container. This creates backpressure rather than continuing to drain a flooding producer.
5. The journal stores job state, counts, and cleanup results, not raw output, input source, environment values, or arbitrary exception messages. Fixture receipts are checked against their expected manifests before recording their synthetic outcomes.
6. Cleanup first lists the exact recorded name, inspects the immutable container ID, and verifies both ownership labels. It removes by ID, then confirms that ID is absent. Missing ownership or an unavailable daemon is a cleanup failure, not permission to remove another container or assume absence.
7. Startup recovery reconciles recorded names, including previously cleaned names, to catch late creation after a client timeout. If resource cleanup completed just before the controller crashed, recovery can still finish the job record. An uncertain create timeout leaves a pending record and blocks additional launches until recovery is attempted.
8. The CLI runs recovery before launching new checks. Resources that cannot be safely reconciled stop this flow. No global Docker prune or deletion of arbitrary host directories is used.

## Live verification

Two real controller processes were used for the crash check. The first committed an ownership record, started a known isolated sleeper, and deliberately exited with `os._exit`, bypassing Python cleanup. The next process acquired the released lock, reopened the journal, verified ownership, removed that worker, and marked the job recovered.

That recovery receipt is in `managed_recovery_results.json`. The final live-check results are in `managed_probe_results.json`:

| Check | Observed result |
| --- | --- |
| Baseline arithmetic | Completed; synthetic check failed. |
| Patched arithmetic | Completed; the same synthetic check passed. |
| Active cancellation | The sleeper emitted its ready message, then was cancelled and removed. |
| Timeout | The sleeper was removed after the short controller deadline. |
| Output flooding | Stopped at the read threshold; 1,056,768 bytes counted and only 65,536 bytes captured. |
| Cleanup | All five live workers were confirmed absent. |
| Production execution | `repository_execution_enabled: false`; `tested: false`. |

The output threshold is checked in 8 KiB chunks, so counted bytes can exceed 1 MiB by one chunk. It is not a guarantee that the container emitted no additional bytes into Docker or OS buffers before shutdown. Container removal and daemon response also take time. The retained Python capture buffer remains bounded at 64 KiB.

Seventeen unit checks cover persistence across reopening, lock exclusion, ownership checks, removal by ID, missing workers, daemon failures, delayed creation, uncertain creation, active cancellation, timeout, output backpressure, capture completeness, and recovery between cleanup and final job recording.

## Limits and operation

The live-check journal is intentionally retained at the workspace path `phase7_step1/managed_probe_journal`. It contains `jobs.sqlite3` and `controller.lock`; it does not contain repository files or raw worker output. These are durable verification records, not leaked temporary source directories. Copying just the result JSON files does not move the journal. Future checks require an explicitly selected journal directory and must use the same Docker engine when recovering its resources.

This pilot serializes controllers rather than implementing a multi-worker scheduler. Recovery runs when the developer CLI is started; no background service or scheduled reaper is installed. It can clean only resources registered in its retained journal, so the journal must not be deleted while work is unresolved. It does not discover all containers on the machine or claim ownership of unknown resources.

The journal directory and local Docker control plane must be trusted. This is not protection against a hostile process running as the same Windows user and altering journal files or labels. The controller does not recover from loss of the journal, hardware storage failure, or Docker-engine replacement.

Cancellation is checked while a worker is active, typically at 50 ms intervals. It is still subject to Docker command timeouts, Python scheduling, and daemon responsiveness. A client-side timeout does not prove Docker stopped processing a create request; pending records and later reconciliation address that uncertainty rather than hiding it.

If cleanup fails, the result stays a cleanup error and the resource record remains available for recovery. Killing a stuck Docker CLI process is not treated as proof that its container is gone. Existing application containers are never deliberately removed.

The new controller does not replace the earlier synthetic demo or enable arbitrary tests. General repository execution still needs a reviewed selection policy, a suitable isolation boundary for the threat model, controlled dependency preparation, and a complete API/job contract. Passing trusted fixtures does not certify safety against hostile code.

## Interview-learning checkpoints

- **Why write intent before creating a worker?** A crash immediately after creation must not leave a resource with no durable ownership record.
- **Why use an OS lock rather than a PID file alone?** A lock is released when its owning process exits. PIDs can be reused and stale text files can misidentify an owner.
- **Why use labels and IDs as well as names?** A name can be reused. Ownership checks identify the intended worker, and deletion by immutable ID avoids deleting a later container with that name.
- **Why isn't a failed inspect enough to prove absence?** The Docker daemon may be unavailable. Absence requires a successful query with no matching resource.
- **Why reconcile already-cleaned names?** A request accepted by Docker can complete after its client times out. A prior observation of absence may not be the final state.
- **Why separate output count and capture size?** The controller needs to detect excessive output without storing an equally excessive amount of data.
- **Why close the pipe at the limit?** It stops draining the producer and applies backpressure while worker shutdown proceeds.
- **Why preserve failed cleanup records?** Recovery needs an auditable list of work that remains unresolved.

## Next step

Define and test a reviewed execution-profile and test-selection policy, then expose only job status and cancellation for trusted synthetic work through a controlled API. Keep repository execution disabled until the profile, artifact provenance, dependencies, and operational recovery have been reviewed together.
