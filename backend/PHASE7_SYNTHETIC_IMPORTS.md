# Phase 7: observing synthetic imports

The pinned worker successfully imported two exact backend-authored files and returned their observed origins and hashes. The full evidence is in `synthetic_import_results.json`.

## What happened

1. Created a tiny package with a literal marker and an addition function. Its folder names resemble Requests solely to exercise the existing provenance policy. These files contain no real Requests code.
2. Built a byte-preserving artifact and recorded its SHA-256 identity. No repository was cloned or read by this probe.
3. Started the previously verified immutable worker image with no network, a read-only root, a non-root user, no host directory mounts and bounded resources.
4. Confirmed that Requests was not discoverable before transfer. Verified the artifact, then wrote it into a fresh temporary directory.
5. Required exactly the two known file paths and their fixed hashes before executing either file. Rehashed the materialized bytes before import.
6. Imported both fixtures and checked the addition result. Observed each module's actual Python import origin and source hash, plus the package search path.
7. Compared that receipt against the original artifact using the existing provenance checker. Both modules matched.
8. Removed the worker's temporary source, the host fixture directory and the disposable container; verified cleanup.

## What this establishes

The transfer, materialization and import-receipt path works for these exact controlled fixtures in this worker image. The receipt checker still reports `runtime_imports_verified: false`: it checks supplied data, while this separate trusted probe supplies the observations. A report from arbitrary hostile source would need stronger controls; it cannot inherit approval from this fixture result.

The runner has no repository, patch, command or image argument. Its image and executable fixture bytes are fixed in the backend. Fixed trusted output permits a small captured receipt; this capture method must not be reused as an arbitrary repository executor.

## Next step

Prepare a narrow execution-profile review that binds source snapshot, artifact hash, immutable image, dependency lock, trusted bootstrap and explicitly allowed tests. Do not enable real repository execution as a side effect. The authentication fallback remains unapproved, and upstream Requests tests have not run.

## Interview learning checkpoints

- What is an import origin? The file Python actually selected for a module, which can differ from the file you intended.
- Why check both origin and hash? A correct-looking path alone does not identify its contents.
- Why check before and after materialization? It links the approved artifact bytes to the files used by the worker.
- Why use harmless fixtures first? They exercise the plumbing without executing third-party repository code.
- Why distinguish a receipt check from an observation? Checking a report's consistency does not independently prove how it was produced.
