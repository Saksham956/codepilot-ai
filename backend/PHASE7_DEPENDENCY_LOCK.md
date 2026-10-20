# Phase 7: candidate dependency lock and source-import provenance checks

Completed October 20, 2026. This prepares inputs for the narrow offline Requests authentication harness. Nothing was installed, no downloaded package code was imported, no repository tests were collected, and no execution profile was approved.

## Candidate dependency inputs

`requests_harness_inputs/requests_harness_dependency_lock.json` records nine exact distributions, wheel filenames, SHA-256 digests, source URLs and dependency metadata. The accompanying `requests_harness_requirements.lock` contains the same exact pins and hashes. The `wheels/` directory holds the verified archives as data, not an installed environment.

| Package | Candidate version |
| --- | --- |
| certifi | 2026.10.18 |
| charset-normalizer | 3.5.2 |
| idna | 3.20 |
| iniconfig | 2.3.1 |
| packaging | 26.3 |
| pluggy | 1.7.0 |
| pygments | 2.21.0 |
| pytest | 8.4.2 |
| urllib3 | 2.8.0 |

These are concrete candidate inputs chosen from public metadata for this review, not general package recommendations or a security endorsement. Their total archive size is 2,188,752 bytes. Exact hashes are in the lock, so later upstream releases cannot silently alter these inputs.

Requests itself is deliberately excluded. The library under test must come from the intended pinned baseline or validated patched source snapshot. Installing an unrelated Requests wheel into the worker could make a test pass against the wrong code.

## What was verified, step by step

1. The fixed roots are Requests' four default runtime dependency ranges plus pytest `>=6.2,<9`. Optional Requests extras and the upstream full development suite are outside this narrow harness.
2. `prepare_requests_dependency_lock.py` reads PyPI metadata, selects non-prerelease universal Python wheels compatible with the declared target, and follows active dependency requirements. It does not use pip, build source distributions, or invoke package-controlled code. Selection is a bounded pilot algorithm that fails if it cannot stabilize; it is not a general-purpose dependency resolver.
3. The target metadata environment is Linux, x86-64, CPython 3.13.0 with no extras. Unknown kernel release/version markers require separate review rather than borrowing values from the Windows host. This is a declared planning target, not a measurement of the existing Docker image.
4. Each downloaded archive matched its published PyPI digest. Only archive structure and bounded metadata were inspected; no wheel contents were extracted or executed. Source builds, `.pth` startup files and distribution script files are outside this policy.
5. The lock's dependency closure was rechecked against the actual wheel metadata for that target. The checker also verifies constraints, package/version identity, wheel tags, whole-archive bytes, and agreement between the text and JSON lock files.
6. `verify_requests_dependency_lock.py` repeats those checks offline. Its result is `candidate_lock_verified_offline`, with nine packages and `metadata_closure_verified: true`. `runtime_image_verified`, `packages_installed`, and `execution_approved` remain false.

Hash-checked, pinned inputs help constrain package preparation. They do not prove that package code is benign, compatible in practice, or free of vulnerabilities. Worker construction remains a separately controlled step. [Pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)

## Source-import provenance rules

`import_provenance_policy.py` checks the consistency of a proposed worker receipt with the verified source artifact and the worker-owned input directory. It does not import Requests or collect observations itself.

The first scope covers exactly:

- `requests`, expected at `src/requests/__init__.py`.
- `requests.auth`, expected at `src/requests/auth.py`.

For each, a future trusted worker must record its actual origin and source hash. Its package search path must identify the intended materialized package directory. The checker rejects an installed site-packages copy, another job's directory, traversal/noncanonical paths, missing or duplicate observations, extra package search locations, stale file hashes and a different artifact identity.

Baseline receipts must match the pinned baseline artifact. Patched receipts must match the validated patched artifact; they must not be compared to a baseline-only hash. The worker root is supplied by the trusted controller, not accepted from a public request.

The result is deliberately named `receipt_consistent_not_runtime_verified`. A self-reported path is not an independent proof against hostile code that can alter interpreter state. Runtime observation and suitable isolation are still required. Other imported modules, dependency packages, and Requests' package aliases need separate provenance rules; this checker does not claim to cover every loaded module.

## Beginner verification

From the backend folder, verify the wheel data offline:

```powershell
& .\venv\Scripts\python.exe .\verify_requests_dependency_lock.py `
  --inputs .\requests_harness_inputs `
  --report "$PWD\dependency-lock-check.json"
```

Expected result: `candidate_lock_verified_offline`, nine packages, no installation, no verified runtime image, and no execution approval. The recorded result is `requests_dependency_lock_results.json`.

Do not treat the `.lock` file as an instruction to install into the backend. The networked preparation script is an explicit review tool; rerunning it can select newer candidate versions and requires another review. There is no automatic refresh on application startup.

## Interview-learning checkpoints

- **Why keep wheel archives without installing them?** Input integrity and dependency planning can be checked before permitting package execution.
- **Why resolve for Linux when developing on Windows?** The future worker's marker environment must determine its dependencies, not the host developer environment.
- **What does dependency closure mean here?** Every active declared dependency has a compatible exact selected package for the stated metadata target.
- **Why exclude the library under test from dependency installation?** Tests must exercise the selected source snapshot rather than an incidental installed copy.
- **Why compare both file location and hash?** A matching name or version alone does not identify the intended file contents.
- **Why is a consistent receipt not runtime verification?** Checking reported data and observing a real import are different operations.

## Verification and remaining work

Fifteen trusted checks cover real archive/hash verification, missing transitive inputs, changed versions, lock-file drift, installed-package substitution, wrong job roots, changed source hashes, package-path injection, artifact mismatch, and data-only operation. Fixtures for provenance contain deliberate `raise` statements and were never imported.

The existing synthetic API and disabled repository executor were not changed. The downloaded wheels are retained as explicit candidate inputs, not temporary running processes.

Next: review and construct a dedicated offline worker image from the locked inputs, record its exact runtime environment and image identity, and re-evaluate the lock for that observed environment. Then verify import observations using controlled fixtures before permitting any Requests source import. The repository profile remains unapproved until those steps and the execution threat model are reviewed.
