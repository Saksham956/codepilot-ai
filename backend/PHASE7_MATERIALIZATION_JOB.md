# Phase 7: materialization and a real synthetic worker job

Implemented October 20, 2026. The job now exercises package verification, file creation, a trusted arithmetic check, comparison, and cleanup in two real isolated workers. It does not enable execution of repositories or AI-generated code.

## Step by step

1. `artifact_materializer.py` verifies the complete package and trusted expected digest before creating any output directory. It creates a new private staging directory under a worker-owned scratch directory. It never merges inputs into an existing project tree.
2. Each verified relative path is created manually. Parent directories must be ordinary directories; files use exclusive creation, so existing destinations are not overwritten. Materialization rejects links and reparse points, checks containment, and verifies the written bytes against each manifest hash.
3. The materializer publishes its directory to the consumer only after all files pass. Files have read-only permissions as a precaution. A context manager deletes the directory on normal completion, consumer failure, interruption, or a failed write before publication.
4. `synthetic_worker_job.py` creates a fixed backend-authored fixture, `calculator.py`, whose `add` function initially subtracts. The existing patch validator changes subtraction to addition in a temporary copy. It creates a separate source artifact for each snapshot.
5. Each artifact is sent through stdin to a fresh container using the previously checked isolation profile and pinned Python image. There are no host mounts, network access, repository dependency installs, or AI-provided commands.
6. A fixed trusted receiver verifies and materializes the files. Before running anything, it requires exactly `calculator.py` and compares its bytes against a hardcoded allowlist of the two backend-authored fixture hashes. A valid artifact digest alone does not authorize execution of arbitrary source.
7. The worker runs only that exact trusted fixture and checks whether `add(2, 3)` equals `5`. The original fixture fails; the patched fixture passes. This is an actual synthetic check inside Docker, not a fake-worker result. No downloaded or user repository code is involved.
8. Each receiver confirms that its materialized input directory was removed before emitting a manifest receipt. The host verifies that receipt and worker exit state, removes the container, and confirms absence. The job removes its host-side snapshots on every synchronous exit path.

## Observed live result

The full receipt is in `synthetic_worker_job_results.json`.

| Field | Observed value | Meaning |
| --- | --- | --- |
| `status` | `synthetic_job_completed` | Both trusted fixture runs and their cleanup completed. |
| `baseline.passed` | `false` | The subtraction version failed the fixed arithmetic check. |
| `patched.passed` | `true` | The addition version passed the same check. |
| `comparison` | `synthetic_failure_to_pass` | Evidence for the controlled fixture only. |
| `synthetic_tested` | `true` | The backend-authored synthetic check actually ran. |
| `tested` | `false` | Repository testing remains unavailable. |
| `repository_execution_enabled` | `false` | Production execution has not been enabled. |
| `host_cleanup_verified` | `true` | All job-owned host snapshots were absent after cleanup. |

Both workers also reported `materialization_cleanup_verified: true` and `cleanup_verified: true`. Their manifests identified different 46-byte contents for the same relative filename, as expected from the patch.

## What this does not provide yet

`run_patch_job` still returns `execution_disabled`, and `/generate-patch` is unchanged. The synthetic job takes no repository, command, patch, image, or test-selection argument. The two permitted source hashes originate in literal backend-owned fixtures, not request data.

Materialization assumes a private worker directory with no hostile concurrent writers. Read-only permissions are not a security boundary against the owning user: that user may change permissions. A `noexec` scratch mount also does not stop an interpreter from reading Python source. The exact-fixture allowlist is therefore essential to this synthetic pilot; arbitrary source is not authorized by successfully writing it.

The controller has a 30-second subprocess timeout, followed by removal of the container. Cancellation is cooperative before and between worker runs; it does not interrupt a currently blocked subprocess immediately. Receipt capture is suitable only for this bounded trusted fixture program, not a general untrusted-output controller. General repository output needs the separately enforced streaming limits designed earlier.

There is no persistent job queue, durable ownership journal, or automatic recovery after service termination. If Docker becomes unavailable during cleanup, the result reports cleanup failure rather than claiming success. The Docker engine and shared Linux kernel remain part of the trust boundary.

## Interview-learning checkpoints

- **What does materialization mean?** Turning verified input bytes into actual files at a fresh controlled destination.
- **Why verify before writing?** Invalid inputs should not leave even a partial published working directory.
- **Why use exclusive file creation?** It prevents accidentally replacing an existing destination or following an existing file entry.
- **Why rehash written files?** It checks that disk contents match the verified manifest, not just the earlier in-memory buffer.
- **Why use one worker per version?** The patched check should not inherit state or side effects from the original check.
- **Why are package hashes insufficient to authorize execution?** Anyone can hash arbitrary code. This pilot separately restricts execution to two exact reviewed fixtures.
- **Why distinguish `synthetic_tested` from `tested`?** A real controlled demonstration must not be misreported as testing the user's repository.
- **Why is cleanup part of the result?** A functional success is incomplete if temporary inputs or worker processes remain behind.

## Verification and next step

Trusted checks cover byte-preserving nested materialization, rejection before output creation, traversal, reparse points, corruption, write failure, interruption cleanup, fresh snapshot comparison, cancellation, unavailable runtime, invalid receipts, worker timeout, configuration rejection, and failed cleanup. The live two-worker job verifies the successful path with the exact fixtures.

Next: add durable job ownership and recovery, then verify restart cleanup, active cancellation, and bounded output capture with trusted fixtures. Keep untrusted repository execution disabled until those controls and a reviewed test-selection policy have been checked together.
