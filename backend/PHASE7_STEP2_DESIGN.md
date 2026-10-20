# Phase 7, Step 2: read a patch report and design isolated testing

Status: design completed; execution remains disabled. This document does not implement or enable a test runner.

## 1. Understand the successful controlled report

Seven in-process endpoint checks passed against the installed backend on October 19, 2026. Git cloning and AI responses were mocked. Fixtures were created locally; no repository code was executed. This verifies the endpoint flow with controlled inputs, not real GitHub or Gemini integration.

The successful fixture proposed two changes in `sample.py`. Its combined diff was:

```diff
--- a/sample.py
+++ b/sample.py
@@ -1,3 +1,3 @@
 raise RuntimeError('DO NOT EXECUTE FIXTURE')
-value = 1
-other = 3
+value = 2
+other = 4
```

Lines beginning with `-` are removed; lines beginning with `+` are added. The unchanged first line deliberately raises an error if executed. It remained source text throughout the check. The check passed because parsing does not run that line.

| Response field | Observed value | Meaning |
| --- | --- | --- |
| `status` | `patch_proposed` | A proposal and validation report were produced. This is not a claim of a correct fix. |
| `all_valid` | `true` | Every proposed file passed exact-match, overlap, and syntax checks. |
| `applied` | `true` | Writing the validated changes to a disposable copy succeeded. |
| `tested` | `false` | Repository behavior was not tested. |
| `application_scope` | `temporary_working_copy` | Application affected a temporary copy only. |
| `temporary_copy_retained` | `false` | That copy was deleted before the response returned. |

An invalid second file produced HTTP 200 with `all_valid: false` and `applied: false`: the request was processed successfully, but its proposal failed validation. An unsafe path instead produced HTTP 422. Always inspect the report, not just the HTTP status.

**Interview checkpoint:** What does a passing validator prove? It proves the checked structural properties. It does not establish compatibility, runtime behavior, or whether the reported issue is a bug.

## 2. The current boundary

`prepare_patch_copy` returns an owner for the temporary patched directory and a validation report. `main.py` immediately calls that owner's cleanup method. Therefore, a future runner cannot reuse the current response to locate or execute the patched files.

Keep `/generate-patch` as a static proposal operation. Add a separate job workflow later, with explicit ownership of the clone and patched copy. Do not return arbitrary filesystem paths for clients to execute.

Proposed lifecycle:

1. Create a private job directory and clone into it.
2. Generate a proposal and validate the full batch.
3. On invalid validation, return the report and stop.
4. On success, keep both original and patched copies until the job finishes.
5. Check that an approved isolated worker is available and its restrictions have been verified. If not, return `execution_disabled`; never fall back to running locally.
6. Run the approved test selection on original and patched snapshots in separate fresh workers with the same environment and limits.
7. Collect bounded results and compare them.
8. Destroy workers and copies on success, failure, timeout, cancellation, or worker crash. Retain only permitted reports for a defined retention period.

**Interview checkpoint:** Why use `finally` or a context manager? Resource ownership must cover every exit path, including exceptions, rather than only the successful response.

## 3. Worker requirements before any repository execution

These are proposed requirements, not controls currently provided by CodePilot. The isolation technology must be selected and verified separately. A temporary directory, Python virtual environment, subprocess timeout, or ordinary Windows user process alone does not meet this design.

| Area | Required worker behavior |
| --- | --- |
| Host access | An ephemeral isolation boundary appropriate for hostile code; no host project, home directory, drive, or container-engine control socket exposed. |
| Credentials | No inherited backend environment, Gemini key, Git credentials, SSH agent, cloud tokens, or `.env` mounts. Start with an explicit minimal environment. |
| Network | Deny outbound access, including DNS, local services, and metadata endpoints. Verify this from a controlled worker fixture. |
| Filesystem | Import a copied source snapshot as data; read-only input where practical, bounded writable scratch space, no shared writable directories between jobs. |
| Identity | Unprivileged worker identity with minimal permissions and no ability to change host security settings. |
| Process control | Limit CPU, memory, process count, disk use, output size, and elapsed time. Enforce limits outside the repository process. |
| Cleanup | Terminate the entire worker on timeout or cancellation, not just the parent Python process. Reap abandoned workers after service restarts. |
| Commands | Backend-owned argument lists; no shell strings or commands supplied by AI output or repository text. |
| Dependencies | A trusted, versioned worker image prepared separately. No automatic `pip install`, build scripts, or repository setup hooks. Missing dependencies produce a clear result. |
| Outputs | Treat stdout, stderr, filenames, and test reports as untrusted data. Bound, escape, and label output before display. |

Proposed initial limits for a small synthetic pilot: 60 seconds wall time per run, one CPU, 512 MiB memory, 64 processes, 256 MiB writable scratch, and 1 MiB combined captured output. These values are a starting policy for review, not measured capacity or implemented enforcement.

Do not silently truncate and mark a run passed: report `output_limit_exceeded`. A timeout, memory kill, missing dependency, or worker failure is not a normal test failure and must remain distinguishable.

**Interview checkpoint:** Why enforce limits outside the child process? Untrusted code can ignore cooperative limits, start descendants, or overwhelm resources before application-level checks react.

## 4. Test selection and dependency policy

Begin with a backend-owned synthetic test selection. Later, a human-reviewed job policy may select repository tests. AI-proposed `tests_to_add` remains explanatory text until reviewed and converted into an approved test artifact.

Test collection is itself execution: imports, plugins, configuration, and fixtures can run before a single test body starts. All collection must occur inside the same isolation boundary. Do not import repository modules on the backend to discover tests.

Worker launch arguments must come from a reviewed execution profile, not arbitrary request fields. Disable automatic plugin discovery where the selected framework supports it; an allowed command is only one layer, and does not replace isolation.

If the prebuilt worker lacks dependencies, return `dependency_unavailable`. Designing a separate dependency preparation service can come later; package installation may run build code and must not happen on the backend host.

**Interview checkpoint:** Why avoid shell command strings? Passing a fixed argument list avoids shell interpretation, but the invoked test framework still executes repository code and still needs isolation.

## 5. Compare baseline and patched runs honestly

Use identical test selection, worker image, and limits for both snapshots. Record the source revision, proposal hash, image identifier, and execution profile so results can be traced to exact inputs.

| Original result | Patched result | Interpretation |
| --- | --- | --- |
| Relevant test fails | Same test passes | Evidence that this test's behavior improved; other checks still matter. |
| All selected tests pass | All selected tests pass | No selected regression detected; the reported bug may not be covered. |
| Selected tests pass | Any selected test fails | Possible regression; do not approve the patch automatically. |
| Test collection fails | Test collection fails | Inconclusive; investigate environment or compatibility. |
| Either run times out or hits a resource limit | Any | Inconclusive execution result; do not label the patch correct. |

For the earlier Requests proposal, passing syntax would not resolve the Latin-1 versus UTF-8 compatibility question. Future tests would need explicit expected credential bytes and a separately justified compatibility policy.

Proposed execution report fields:

```json
{
  "execution_status": "execution_disabled",
  "reason": "Verified isolated worker is not configured",
  "tested": false,
  "baseline": null,
  "patched": null,
  "comparison": "not_run"
}
```

This is a future contract example, not a new response emitted by the current endpoint. During execution, use explicit states such as `queued`, `running`, `completed`, `timeout`, `resource_limit`, `dependency_unavailable`, and `worker_error`. Set `tested: true` only when the intended selected tests completed for both snapshots; expose partial execution through the per-run states.

**Interview checkpoint:** Why run the original version too? Existing failures must be distinguished from regressions introduced by the patch.

## 6. Implementation order and acceptance checks

1. Implement a disabled-by-default runner interface and job lifecycle using a fake worker. Prove no worker launches unless policy permits execution. Keep the current endpoint behavior intact.
2. Select and configure the real isolation boundary. Use only trusted synthetic fixtures to verify denied network access, absence of host secrets, bounded filesystem access, process limits, timeout cleanup, and output caps.
3. Wire baseline and patched runs into the job lifecycle. Verify cleanup after worker crashes, cancellation, service restart, and partial completion.
4. Validate comparison and reporting with deterministic synthetic pass/fail fixtures.
5. Only after those controls pass, enable a narrowly reviewed repository test profile. Do not enable unrestricted tests or dependency installation by default.

The next coding task is item 1: a disabled runner interface with fake-worker tests. Real repository execution remains a later step.
