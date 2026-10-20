# First real repository-source execution

Requests 2.32.5 source executed successfully inside the dedicated isolated worker. Seven backend-authored offline smoke checks passed. This is a baseline characterization run, not an upstream test-suite run or a patched-versus-baseline comparison.

## Scope and results

- Repository: https://github.com/psf/requests
- Pinned revision: b25c87d7cb8d6a18a37fa12442b5f883f9e41741
- Source artifact: f31134cffb53a184b11ae553107bd4a620a65db9d7a18aebb1e4405137c9ac95
- Immutable worker: sha256:a708b1ec2b38a51fc947c5907a61e92a5781d9e9304ab230f37eb1a74520d654
- Dependency image and lock: the previously verified nine-package worker.
- Entry point: real_requests_smoke.py, operator-only; no API or generic runner enablement.
- Allowed actions: import the pinned Requests package and call fixed Basic Auth/header-preparation checks. No HTTP request is sent.

ASCII, Latin-1, byte and empty credentials produced the expected Basic Auth header. Non-Latin-1 usernames and passwords each raised UnicodeEncodeError as expected. Preparing a request with ASCII credentials produced the expected Authorization header without sending it.

A passing Unicode case means the limitation was reproduced. It does not mean broader Unicode authentication now works.

## Step by step

1. Fetched the pinned commit and tree, then verified every selected source file against its Git blob identifier. Only 18 ordinary Python files under src/requests were included. No setup scripts, repository configuration, upstream tests or conftest hooks were included.
2. Parsed source as data and inspected the import list. Optional dependencies are absent from the worker; the installed runtime dependencies were already locked and checked.
3. Built the artifact, pinned its digest in the runner and verified it again before creating a container. Altered artifacts are rejected before Docker is called.
4. Started the immutable image using the established no-network, non-root, read-only-root and resource-limited configuration, without host directory mounts.
5. Materialized verified source under a fresh worker temporary directory and imported the real package there. Checked the two target module origins and hashes; additionally rehashed loaded source modules from this snapshot. Dependency aliases are outside that source-module check.
6. Ran the seven fixed checks. Captured at most 64 KiB, enforced a 1 MiB output threshold and a 30-second deadline. These controls bound runaway output and execution; they do not make Python introspection a proof against hostile code.
7. Verified materialized source cleanup and removal of the disposable container. Saved the observed results and trusted bootstrap hash in real_requests_smoke_results.json.

The receipt checker remains a consistency checker. Its runtime_imports_verified=false field is intentional; the surrounding run supplies observations for this particular pinned source, not a universal provenance guarantee.

## What remains

The production executor and generic repository API remain disabled. No candidate patch was applied. No upstream test suite ran. The earlier per-field UTF-8 fallback remains unapproved because compatibility still needs an explicit policy decision.

Next, expand the fixed compatibility cases and decide whether an opt-in encoding policy is appropriate before proposing a patch. Then review explicit upstream tests and their dependencies/configuration separately. Do not label the existing seven smoke checks as the full Requests test suite.

## Interview checkpoints

- Why start with a baseline? It establishes existing behavior before a proposed change can be compared.
- Can a test pass while reproducing a bug? Yes: a characterization test can expect the current exception.
- Why pin a commit and verify blobs? A branch or tag can move; the run needs identifiable source bytes.
- Why avoid setup.py and pytest discovery here? They can execute additional repository code beyond the selected behavior.
- Why keep the generic executor disabled? One fixed successful run does not establish safe operation for arbitrary repositories and commands.
