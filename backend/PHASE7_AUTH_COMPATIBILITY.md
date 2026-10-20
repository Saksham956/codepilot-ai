# Phase 7: authentication compatibility specification and pinned evidence

Completed October 20, 2026. This step returns to the original Requests authentication issue. It defines byte-level expectations and verifies public source/dependency evidence as data. It does not execute Requests, run its tests, approve a patch, install a package, or enable repository execution.

## Decision about the earlier proposals

Do not approve either the unconditional UTF-8 change or the per-field Latin-1/UTF-8 fallback as a general fix on the current evidence.

RFC 7617 leaves the default encoding unspecified for backward compatibility. A server's UTF-8 charset preference is advisory; UTF-8 handling also has normalization considerations. The RFC explicitly discusses why changing defaults can break deployments. Therefore the earlier claim that the standard mandates changing Requests' default to UTF-8 was incorrect. [RFC 7617, sections 2, 2.1 and B.3](https://www.rfc-editor.org/rfc/rfc7617.html)

The pinned helper explicitly encodes string credentials with Latin-1. That supports treating its limitation as compatibility-sensitive; it does not, by itself, prove a maintainer's intent or settle every desired future API behavior. Bytes and string inputs need separate characterization. [Pinned authentication source](https://github.com/psf/requests/blob/b25c87d7cb8d6a18a37fa12442b5f883f9e41741/src/requests/auth.py)

The independent reference model shows two problems: unconditional UTF-8 changes existing Latin-1 credential bytes; per-field fallback can mix encodings when one field needs UTF-8 and the other contains Latin-1 characters. These are compatibility concerns, not observations from executing Requests or a candidate patch.

## Pinned source evidence

The observed tag `v2.32.5` resolves to:

```
b25c87d7cb8d6a18a37fa12442b5f883f9e41741
```

`collect_auth_review_evidence.py` fetched GitHub metadata and seven fixed source/configuration files at that commit. For each file, its Git blob hash matched the pinned tree, and a SHA-256 digest was recorded. Python files were parsed with `ast`; literal dependency declarations were read with `ast.literal_eval`. The setup script was never run. Source bodies were not installed into the backend.

The evidence file is `requests_auth_review_evidence.json`. It records source URLs, hashes, declarations, and two syntax-derived helper-test candidates:

- `tests/test_requests.py::TestRequests::test_set_basicauth`
- `tests/test_requests.py::TestRequests::test_basic_auth_str_is_always_native`

These names came from static syntax inspection, not pytest collection. They remain unapproved targets; imports, fixtures, configuration and coverage still need review. [Pinned test source](https://github.com/psf/requests/blob/b25c87d7cb8d6a18a37fa12442b5f883f9e41741/tests/test_requests.py)

Matching GitHub tree/blob metadata establishes consistency with the observed public revision. It is not independent verification of a publisher signature or a claim that the code is safe.

## Twelve planned compatibility cases

`auth_compatibility_spec.json` contains synthetic credentials, exact expected bytes where applicable, and predicted legacy exceptions. The expectations are static inferences that a future approved runner must characterize against the real library.

| Case | What must be checked |
| --- | --- |
| ASCII credentials | Preserve the existing Basic header. |
| Latin-1 username and password | Preserve exact existing bytes, including non-ASCII Latin-1 characters. |
| Opaque byte inputs | Preserve caller-supplied bytes. |
| String username with byte password | Preserve default string encoding and opaque password bytes. |
| Byte username with string password | Preserve opaque username bytes and default string encoding. |
| Empty fields | Preserve the colon separator and resulting header. |
| Colon in password | Preserve the existing payload representation. |
| Non-Latin username, ASCII password | Record the predicted legacy failure; do not assume a new header is universally acceptable. |
| Non-Latin username, Latin-1 password | Detect a fallback payload that differs from uniform UTF-8. |
| Latin-1 username, non-Latin password | Detect the symmetric mixed-encoding problem. |
| Both fields non-Latin | Separate broader Unicode support from server compatibility. |
| Decomposed Unicode | Keep normalization policy separate from merely choosing an encoder. |

For example, the synthetic username `用户` and password `päss` produce these different reference payloads:

```
Per-field fallback: e794a8e688b73a70e47373
Uniform UTF-8:     e794a8e688b73a70c3a47373
```

Different payloads produce different authentication headers. Avoid treating Base64 success as proof of successful authentication.

Colon-in-username validation, control-character rules, non-string deprecation behavior, negotiation, retries, redirects and proxy integration are explicitly deferred. No validation or normalization change is being added to Requests in this step.

## Dependency evidence

`collect_dependency_review_evidence.py` fetched the PyPI metadata for Requests 2.32.5, downloaded the official wheel into memory, checked its SHA-256 digest, and read only its bounded `METADATA` entry. It did not install or import the wheel and did not extract files to disk. The recorded release requires Python 3.9 or later. [Release metadata](https://pypi.org/pypi/requests/2.32.5/json)

The recorded wheel SHA-256 is:

```
2462f94637a34fd532264295e186976db0f5d453d1cdd31473c85a6a161affb6
```

`requests_dependency_review_evidence.json` lists the declared runtime ranges and optional extras. Exact pins for all dependencies, transitive coverage, reviewed wheels, a compatible pytest environment, and the worker image are still unresolved. This is evidence gathering, not a dependency lock or installation approval.

The Requests wheel inspected here is a metadata reference. A later baseline/patched source test must verify that it imports the intended materialized source, not accidentally an installed unpatched Requests wheel. Import provenance belongs in the future execution checks.

## Try the data-only specification check

From the backend folder:

```powershell
& .\venv\Scripts\python.exe .\check_auth_compatibility_spec.py `
  --report "$PWD\auth-specification-check.json"
```

The checker uses only backend-authored reference models and Python's standard byte encoders/Base64. It imports no Requests code, makes no network calls, runs no candidate patch, and starts no worker. The expected result is `specification_consistent`, `case_count: 12`, and `patch_approved: false`.

`auth_compatibility_review_results.json` records the completed check. Ten trusted unit checks verify the specification, contrast bytes, evidence identities, and the distinction between reference-model results and actual repository test execution.

## Interview-learning checkpoints

- **Why check bytes rather than only text?** Authentication headers are derived from bytes; visually identical text can encode differently.
- **Why isn't UTF-8 support automatically a backward-compatible fix?** Existing credential bytes and server expectations can change.
- **Why can fallback per field be problematic?** It can select different encodings for the two parts of one credential payload.
- **Why keep expected results separate from observed results?** A model can explain a hypothesis without proving that the real library behaves exactly as predicted.
- **Why inspect a setup script with AST rather than run it?** Running it can execute project-controlled code; parsing literal declarations only reads data.
- **Why is one verified wheel not a complete environment?** Its dependencies and test tooling still need exact compatible artifacts and review.
- **Why check import provenance later?** A passing test against an unrelated installed package would not establish that the patched source works.

## Next step

Prepare a concrete, reviewed dependency lock and import-provenance plan for a narrowly scoped offline compatibility harness. Decide whether broader Unicode belongs in an explicit caller/server encoding policy or a supported API extension. Preserve existing default behavior while that decision is unresolved. Repository tests and package installation remain disabled.
