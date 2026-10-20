# Phase 7: repository test and dependency preflight

Completed October 20, 2026. This step adds a static policy helper. It does not approve a repository, install packages, collect tests, start a worker, or add a new API route. The production repository executor and reviewed-profile allowlist remain disabled and empty.

## What changed, step by step

1. `repository_test_policy.py` accepts a fixed plan schema and verified source-artifact bytes. It rejects unknown fields, including commands, environment overrides, installation arguments, plugin options, or a caller-provided approval flag.
2. A plan must name a canonical HTTPS GitHub repository, a full commit object ID, the exact source-artifact digest, and an immutable worker image ID. Branches, tags, mutable image tags, unsafe URLs, and mismatched artifact identities are rejected. Commit origin and image contents still require independent trusted verification.
3. Dependencies must be declared individually with canonical names, exact version tokens, and SHA-256 wheel identities. Ranges, editable-install syntax, URLs, and local paths are rejected. This initial policy is pytest-specific and requires pytest to be declared. It does not resolve or install anything.
4. The dependency lock identity is the SHA-256 hash of the canonical sorted declared pin list. Changing a pin without updating that identity is rejected. This proves metadata consistency, not completeness of transitive dependencies, wheel availability, package compatibility, or installed image contents.
5. Test selection is limited to 1–20 explicit function nodes, optionally inside a test class, in selected `tests/test_*.py` files. Bare directories, bare files, expressions, wildcard selectors, parameter selectors, shell-like syntax, duplicate nodes, and absent test files are rejected. Nodes are not collected or imported, so this does not prove that the named functions exist or can run.
6. The source artifact is independently checked using its trusted expected digest. Existing packaging restrictions on secrets, links, sizes, and setup/plugin files remain in force. This step does not weaken those restrictions to accommodate a repository's defaults.
7. Valid plans receive a deterministic fingerprint with dependency and test order normalized. Their result still has `review_status: not_reviewed`, `eligible_for_execution: false`, and `execution_status: execution_disabled`. The report names the review gaps rather than presenting a schema check as permission to run code.

## Actual Requests review: why its defaults are not approved

To make the review concrete, the public Requests `v2.32.5` files were inspected as text only. This is a tagged reference for discussion, not an approved immutable commit profile and not a claim about whatever revision an earlier clone used.

- Its development requirements include an editable project installation with extras, a pytest version range, and several unpinned packages. Running that file is incompatible with an exact, prebuilt dependency policy. [Requests development requirements](https://raw.githubusercontent.com/psf/requests/v2.32.5/requirements-dev.txt)
- Its pytest configuration enables module doctests and sets the tests directory. A bare pytest invocation would inherit repository-owned selection/configuration rather than an explicitly reviewed backend profile. [Requests pytest configuration](https://raw.githubusercontent.com/psf/requests/v2.32.5/pyproject.toml)
- Its test module imports Requests, urllib3, test-server utilities, and helpers. Selecting one test does not prevent module-level imports during collection. [Requests test module](https://raw.githubusercontent.com/psf/requests/v2.32.5/tests/test_requests.py)
- Its conftest file defines HTTP fixtures and a certificate-backed local-server fixture. That file is currently rejected by CodePilot's source-input policy. Removing this restriction or supplying missing plugins automatically would require a separate reviewed change. [Requests fixtures](https://raw.githubusercontent.com/psf/requests/v2.32.5/tests/conftest.py)

The decision is **not approved for execution**. Do not run its requirements file, install the project, or collect its tests on the backend host. A future candidate needs an exact revision, focused compatibility assertions, reviewed import/fixture coverage, locked dependency artifacts, and a separately prepared verified worker image.

The earlier Latin-1/UTF-8 authentication proposal still needs a compatibility decision based on expected credential bytes and server behavior. Neither this policy helper nor the arithmetic demonstration establishes that proposal as a correct fix.

## Dependency and collection rules for a future reviewed profile

Package preparation must be separated from repository execution. Define a backend-owned dependency lock, review every required distribution and its transitive dependencies, and prepare an immutable worker image in a separately controlled process. No automatic dependency installation or source builds occur in the test job or on the backend host.

Pip documents hash checking for all requirements and dependencies, and using binary-only distributions to avoid source distributions. These controls help constrain installation inputs; hashes do not establish that a package is benign. No pip command was executed in this step. [Pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)

A future pytest profile must review configuration, imports, plugins, conftest files, fixtures, doctests, and environmental options before collection. Collection belongs inside the isolation boundary because it can load repository code before a test body runs. Use backend-owned settings rather than accepting arbitrary pytest arguments from an API caller. [Pytest configuration](https://docs.pytest.org/en/stable/reference/customize.html)

## Beginner walkthrough

Run the installed data-only demonstration from the backend folder:

```powershell
& .\venv\Scripts\python.exe .\check_repository_preflight.py `
  --report "$PWD\repository-preflight-demo.json"
```

This creates temporary synthetic source files, packages them, checks a synthetic plan, writes the report, and removes its temporary files. It performs no network requests and does not need Docker or Gemini. The example commit, image, wheel hash, repository, and `0.0.0` package version are deliberate placeholders, not recommended or available dependencies.

Expected result:

```json
{
  "plan_valid": true,
  "review_status": "not_reviewed",
  "execution_status": "execution_disabled",
  "eligible_for_execution": false,
  "tested": false,
  "repository_execution_enabled": false
}
```

Read the blockers: profile approval, source origin, dependencies/image, collection behavior, and the disabled executor. `repository_preflight_results.json` contains the recorded demonstration result.

## Interview-learning checkpoints

- **Why distinguish validation and approval?** Well-formed data can describe an unsafe or unsuitable workload. Schema checks do not grant trust.
- **Why require a commit ID rather than a branch?** A branch can move; a job needs a specific declared source revision. The declaration still needs provenance verification.
- **Why hash the lock list and each wheel?** The list identifies the requested environment; wheel hashes identify distribution bytes. Neither proves safety or complete dependency coverage.
- **Why not install requirements automatically?** They can request editable projects, source builds, extra downloads, or unreviewed code paths.
- **Why isn't selecting one test enough?** Collection may import its module, helpers, fixtures, or plugins before that test starts.
- **Why doesn't `plan_valid: true` enable execution?** The helper checks structure and consistency only; human-reviewed profile approval and runtime enforcement are separate gates.

## Verification and next step

Eighteen trusted checks cover immutable source/image identifiers, artifact integrity, exact pins, lock consistency, explicit nodes, missing files, duplicate selection, bounds, order-independent fingerprints, rejection of self-approval and commands, and refusal to perform network/process/filesystem operations during preflight itself.

The helper is not wired to the synthetic API and contains no execution path. The allowlist cannot be populated by request fields. A future approval mechanism would require a deliberate backend change and actual reviewed evidence.

Next: define a concrete backend-owned compatibility test specification for the authentication issue, then verify the chosen source revision and dependencies as data. Do not collect or execute Requests code until those inputs and the runtime policy have been reviewed together.
