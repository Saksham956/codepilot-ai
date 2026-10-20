# Approved upstream baseline and learning comparison

ItsDangerous 2.2.0 is pinned to commit 096c8d42545d3b68ea21a4f890fb2b2d8979c0bd. Nine Python files (package source and one upstream test file) were fetched as data and checked against Git blob identifiers. The artifact digest is fa57efaa367a3e4c9cb34c5069e49eda83cbbe307e028cef39536159f619483e.

## What happened, step by step

1. Reviewed the encoding test file and its package imports. Four explicit functions cover eight parameterized cases. There are no ancestor conftest files in the pinned tree. The selected tests need pytest; runtime package code uses standard-library modules. A typing_extensions import is guarded by TYPE_CHECKING and is not needed at runtime.
2. Reused the previously verified immutable worker image and locked pytest environment. No installation occurs during testing. Worker networking is disabled and no host directories are mounted.
3. Ran pytest with explicit test nodes, an empty configuration, conftest loading disabled, plugin autoload disabled by the image, and pytest cache disabled. This deliberately runs the selected tests in a reviewed minimal environment, not the repository's complete default test environment.
4. Ran unchanged source twice: eight cases passed each time.
5. With the user's explicit choice, applied one deliberate Base64 bug to a temporary copy using the existing full-file patch validator. Two upstream cases failed. This is a learning exercise, not an actual upstream bug discovery.
6. Validated the exact restoration in another temporary copy. Eight cases passed; the restored artifact digest matched the original byte-for-byte.
7. Checked imported package source origins/hashes, removed containers and source directories, and verified the original snapshot remained unchanged.

The fixed runner accepts an operator-selected input directory but rejects source differing from its pinned upstream artifact. API requests cannot supply source paths, images, tests or commands. Only the fixed exercise is exposed as a workflow option.

## Read the result correctly

Baseline: 8 passed, repeat: 8 passed, deliberate bug: 6 passed and 2 failed, restoration: 8 passed. Four separate containers ran. Only eight selected cases ran per container; this is not the entire ItsDangerous test suite. The candidate was a deterministic restoration, not an AI-produced repair. Syntax validation's nested tested=false field describes that validator alone; the outer comparison records the actual subsequent pytest execution.

## Interface

Select the ItsDangerous learning comparison workflow, submit it, and inspect the report, history and downloaded repair diff. It uses the background report-job queue. Cancellation remains cooperative at the report-operation boundary. Forced process crashes are not yet covered by a durable resource journal for this new runner.

## Learning checkpoints

- A parameterized test function can produce multiple test cases; four functions here produce eight cases.
- A baseline establishes behavior before a candidate is tested.
- A seeded defect is useful for proving the comparison pipeline detects failures; it must never be described as an upstream bug.
- Matching restored artifact hashes proves byte identity, while passing tests establishes only the selected behavior.

## Remaining completion work

Real Gemini integration verification, approved AI-generated candidate execution, durable Docker crash recovery for these real-source runners, active-operation cancellation, fresh-machine environment verification and deployment hardening remain. Current execution is profile-specific; arbitrary repository execution stays disabled.

## Real provider verification completed
One explicitly authorized live Gemini request returned a proposed repair. The prompt supplied the expected one-line restoration, so this verifies integration rather than autonomous discovery. The returned candidate artifact matched the approved upstream digest exactly and then passed all eight selected tests in a fresh isolated worker. Evidence: gemini_learning_repair_results.json. No further provider request was made.

