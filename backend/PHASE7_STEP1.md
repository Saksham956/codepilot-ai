# Phase 7, Step 1: validate a batch and apply it to a disposable copy

## What changed, step by step

1. `main.py` sends the complete proposal to `prepare_patch_copy` once. The selected filenames form an allowlist. Prompt snippets help the AI propose edits, but are no longer treated as the authoritative file contents.
2. `patch_validator.py` reads each complete UTF-8 Python file. It finds each original snippet exactly once in the unchanged file, then rejects duplicate or overlapping edits. Changes cannot target text introduced by an earlier change.
3. Edits are applied from the end of the file toward the beginning, in memory. This keeps the original character positions correct. The validator parses the complete final file with `ast.parse`. Parsing checks Python grammar without importing or running the source. Each report contains a combined diff per file.
4. Every file must pass before a temporary working copy is created. The copy excludes `.git`. Validated contents are written only there. If copying or writing fails, that unpublished directory is removed and the request fails. The original clone is never patched. This is batch isolation through a disposable directory, not a claim that multiple filesystem writes are one atomic operation.
5. The endpoint returns the report, deletes the patched copy, and then cleans up the original clone. `applied: true` means application succeeded in the temporary copy. `application_scope: temporary_working_copy` and `temporary_copy_retained: false` explain its lifetime. `tested` remains false. Empty or invalid batches have `all_valid: false` and `applied: false`.
6. Paths must be allowlisted relative Python filenames. Absolute paths, parent traversal, drive paths, alternate data streams, reserved Windows names, and `.git` targets are rejected. File components and repository trees reject symlinks and Windows reparse points (including junctions). Ordinary Windows CRLF files retain CRLF; mixed newline styles require manual review.
7. `github_service.py` clones without checkout, uses an empty Git template and isolated Git configuration, disables hooks and recursive submodules, checks metadata size, and checks Git tree entries for symlinks before checkout. It then checks the working tree before returning it to the analyzer. Git subprocesses have timeouts and do not use a shell.

## Interview-learning checkpoints

- **Why validate full files?** A snippet can look correct while breaking indentation or syntax elsewhere in its enclosing function.
- **Why validate changes together?** Two replacements can overlap, or each can be syntactically invalid alone while their final combination is valid. The final file is the unit of syntax validation.
- **Why replace from right to left?** Editing later positions first leaves earlier offsets unchanged.
- **Why isn't `resolve()` enough?** Path containment and link detection answer different questions. Reject path traversal before accessing files, then reject symlinks and junctions along the path.
- **What prevents partial application?** First validate everything in memory; then write to a disposable, unpublished directory. Remove it on failure. Never change the source clone.
- **Does valid syntax prove a correct fix?** No. It does not prove runtime behavior, compatibility, security, or that the issue is a bug. In particular, the earlier Requests authentication proposal still needs a separate compatibility review.
- **Why isolate Git configuration?** Hooks and configured checkout filters may start programs even if Python source is never explicitly run.

## Verification and boundaries

Trusted checks use only synthetic local files and mocked Git commands. They cover full-file validation, combined syntax, multiple edits, ambiguous matches, overlaps, unknown targets, unsafe paths, empty proposals, CRLF preservation, metadata exclusion, write-failure cleanup, reparse-point rejection, and Git command isolation/order. A real symlink fixture is skipped when Windows denies symlink creation permission.

No downloaded repository code, package installation, repository tests, server startup, Gemini calls, or network clones were executed during this step. The trusted tests are `test_safe_patching.py` and `test_clone_safety.py`.

This assumes the clones and temporary directories are private to the backend, with no concurrent external process replacing files. It is not a sandbox against a hostile process running as the same Windows user. The metadata size limit is checked after downloading; checkout disk usage and total network transfer are not hard resource limits. Fully sandboxed execution and stronger resource isolation belong to a later step, before untrusted tests are enabled.
