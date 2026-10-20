# Phase 7: bounded source artifacts and verified data transfer

Implemented October 20, 2026. This step packages explicitly selected source as data and verifies its receipt in an isolated container. It does not execute that source, enable repository tests, or add an API route.

## Step by step

1. A trusted backend caller supplies a private source directory and an explicit list of filenames to `build_source_artifact`. No directory discovery or automatic recursive copying happens. Passing a directory does not include its other files.
2. A conservative Python-only path policy runs before file reads. It rejects absolute paths, traversal, hidden components, alternate data streams, Windows reserved names, sensitive directories and filenames, and several Python setup/plugin entry points. Duplicate names, case aliases, and file/directory prefix collisions are rejected.
3. Existing filesystem containment checks reject symlinks and Windows reparse points in selected paths. Hard-linked files are also rejected. Unselected files are not opened. The builder checks for ordinary changes while reading, but assumes a private snapshot without concurrent hostile writers.
4. Limits are fixed for this first policy: 100 files, 256 KiB per file, 4 MiB total source bytes, and 6 MiB encoded package bytes. File reads stop at the per-file limit plus one byte.
5. Only UTF-8 source is accepted. Null bytes and several common secret patterns cause rejection. The builder rejects the entire batch rather than silently dropping a selected file.
6. Source bytes are base64-encoded inside a deterministic JSON envelope. It has no archive links, compression, extraction paths, timestamps, host paths, or executable permissions. Files are sorted; exact original bytes, including CRLF line endings, are preserved.
7. The manifest records each relative path, byte size, and SHA-256 digest, plus total source bytes and the complete package digest. It contains no source content. Construction happens in memory; any failure produces no package on disk and no changes to source files.
8. `verify_source_artifact` independently checks the package digest, format, count, paths, limits, content markers, per-file sizes and hashes, ordering, and canonical encoding. It returns a manifest rather than extracting files or importing code.

## What the transfer probe proved

`artifact_transfer_probe.py` creates a fixed trusted fixture containing one selected Python file, an unselected Python file, and a synthetic `.env`. Only the selected file is packaged. Its first line raises an error if executed; it was never executed.

The probe streams the bounded package through container stdin. The fixed trusted receiver uses the same reviewed verifier functions and independently validates the received bytes. Its manifest must exactly match the host manifest. Sharing policy code prevents drift; the live comparison additionally checks transport integrity, not independence of the verification algorithm.

No host folders are mounted. The image identifier and hardened container launch policy come from the preceding trusted-probe step. The receiver only decodes and hashes data in memory; no received file is extracted, compiled, imported, or run. The container is removed even on failure, and absence is checked before reporting success.

Observed live result: `synthetic_transfer_passed`, with one selected file containing 54 source bytes in a 219-byte package. The worker returned the same file and package hashes. `source_executed` and `repository_execution_enabled` were both false; `cleanup_verified` was true. The full receipt is in `artifact_transfer_results.json`.

## Important boundaries

This is a minimal source-input policy, not a complete secret detector. A credential can be embedded in an innocently named Python file or disguised so the current patterns miss it. Human review of selected content remains necessary before handling real private repositories. Hashes prove byte consistency, not safety or authorship. The expected digest must be supplied by a trusted job record; a digest obtained alongside attacker-controlled data does not authenticate it.

Some legitimate files will be blocked, such as `conftest.py`, `setup.py`, hidden packages, non-ASCII paths, non-UTF-8 Python, or code containing credential-like test fixtures. There is no public override flag. A future reviewed policy can support necessary cases explicitly. This first artifact is not sufficient to run arbitrary repositories and must not trigger package installation to compensate for missing files.

Links are checked for selected paths only; unselected content is ignored rather than copied. Files are read from an assumed private clone. These checks do not defeat a hostile process running as the same Windows user and racing filesystem operations.

The live receiver is a fixed trusted program with bounded input and a bounded manifest response. It is not a general-purpose output-capture adapter for untrusted processes. Like the earlier probes, it lacks a persistent job journal and service-restart cleanup. Passing this check does not enable `run_patch_job`, which still fails closed.

## Interview-learning checkpoints

- **Why explicit selection instead of copying the repository?** It reduces the material entering the worker and avoids incidentally including unrelated configuration or secrets.
- **Why base64?** It preserves exact bytes in JSON. It is encoding, not encryption or a security boundary.
- **Why hash each file and the whole package?** File hashes identify exact file contents; the package hash also covers the selected names and encoded structure.
- **Why does a hash not prove safety?** Malicious or secret data can have a perfectly valid hash. Trust and policy checks are separate.
- **Why reject an entire selected batch?** Silently omitting a necessary file can produce misleading test results and conceal policy failures.
- **Why verify again inside the worker?** Transport and package integrity should be checked at the receiving boundary, before any future extraction or execution.
- **Why not unpack the package yet?** Checking data transfer and checking safe materialization are separate steps. This phase proves receipt without increasing execution scope.

## Verification and next step

Trusted checks cover the policy, deterministic manifests, limits, unsafe paths, links, secret markers, byte preservation, malformed packages, tampering, and all-or-nothing construction. Transfer checks also cover incorrect receipts, timeout, and cleanup failure.

Next: safely materialize verified inputs in a fresh worker-owned location, then build a synthetic baseline/patched job across the real worker lifecycle. Keep repository execution disabled until input handling, command selection, job ownership, cancellation, and recovery have been checked together.
