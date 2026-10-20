# Phase 7: offline worker image

The dedicated review worker is built and its runtime checks passed. This is progress toward repository testing, not approval to execute a repository.

## What happened, step by step

1. Verified the existing nine wheel archives against the dependency lock, including hashes and dependency metadata.
2. Created a minimal build context containing only the Docker recipe, lock and wheels. No repository files or project environment files were included.
3. Built from the pinned Python base. Package installation used local wheels, required hashes, disabled package-index access and ran in a build step with networking disabled. Dependencies were installed inside the image, not into the backend environment.
4. Observed the running worker using fixed trusted Python code. It uses `/opt/worker-venv`, Linux x86_64 and Python 3.13.16. All nine locked versions match. The base's pip is also present. Requests is absent and cannot be discovered before source transfer.
5. Rechecked dependency Python constraints and active dependency markers against the actual runtime, rather than assuming the candidate target's 3.13.0 patch version.
6. Passed permissions/network/root-filesystem, scratch capacity, process limit, memory limit, timeout and output limit probes. Probe containers were removed and absence verified.

The immutable image reference and detailed evidence are in `offline_worker_validation_results.json`. The local tag is `codepilot-requests-worker:review-5fd3ed128ed3`; future execution policy must pin the recorded immutable reference rather than trusting a mutable tag.

## Limits and next step

No real Requests source, candidate authentication patch or repository tests ran. Production execution stays disabled. The probes verify specific controls on this Docker host; they are not a proof against every possible malicious program. The build's package installation was offline; resolving pinned base metadata can still involve Docker registry communication.

Next, observe import origins using a tiny backend-authored synthetic package, check its file hashes against the transferred artifact, and bind the resulting evidence to a narrow reviewed execution profile. Actual repository tests remain a separate milestone. Import-origin observation has not yet been performed on this new image.

## Interview learning checkpoints

- Why pin both wheel hashes and an immutable image reference? Version names and tags alone do not identify exact bytes.
- Why recheck dependencies against the running Python? A planned target and the actual runtime can differ.
- Why keep dependencies separate from repository source? It makes the environment reproducible and lets source provenance be checked independently.
- Why test cleanup after timeout and memory failure? Failure paths can otherwise leave resources running.
- Does a successful isolation probe make arbitrary repository execution approved? No. Execution policy, source identity and the allowed test scope still need review.
