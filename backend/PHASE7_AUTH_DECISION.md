# Real-source authentication compatibility decision

Decision: preserve Requests' default Latin-1 behavior and opaque byte credentials. Do not approve the earlier automatic UTF-8 fallback proposal.

## Evidence, step by step

1. Expanded the fixed real-source worker to run the existing 12-case compatibility specification, in addition to the original seven smoke checks. The specification is hash-pinned before the trusted worker script is generated.
2. Executed the pinned Requests source in the same isolated worker. All 12 measured outcomes matched the previously specified baseline. Temporary source and container cleanup passed.
3. Compared those measured outcomes with backend-authored candidate encoding models. These models are not executions of a patched Requests checkout.
4. Unconditional UTF-8 changes three existing successful header cases: a Latin-1 pair, string username with byte password, and byte username with string password.
5. Per-field fallback produces bytes different from uniformly UTF-8 encoding the complete string pair in two cases: a Chinese username with a Latin-1 password, and a Latin-1 username with a Chinese password. It preserves the measured successful legacy cases but changes previously raised exceptions and does not establish a coherent server encoding policy.
6. Recorded the decision in real_auth_compatibility_decision.json. No source patch was applied or approved.

For example, a UTF-8 Chinese username combined with a Latin-1 password containing ä yields a credential pair that is not uniformly UTF-8. Producing a header does not prove that the server can decode or authenticate it.

## Scope

The run establishes local encoding behavior, not server authentication interoperability. No network request or upstream test suite ran. It does not establish maintainer intent by itself. The source's explicit Latin-1 choice and the compatibility results justify preserving that default for this task.

An explicit caller-selected encoding, with both string fields encoded consistently when the server's expected encoding is known, is a possible opt-in direction. Existing byte credentials are preserved by the measured baseline. Server requirements and normalization must be specified before claiming a general Unicode solution.

## Next project step

Expose this useful outcome in CodePilot's report flow: issue reproduced, compatibility risk found, no compatible patch approved, baseline checks passed, and exact test scope. A patch generator should be able to return an evidence-backed no-change decision rather than always producing a patch. The generic production repository executor remains disabled.

## Interview checkpoints

- What is a regression? A change to behavior relied on by existing users, including the exact transmitted bytes.
- Why can a fallback be wrong even when encoding succeeds? The server must interpret the resulting byte sequence consistently.
- Why distinguish measured and modeled results? The baseline ran real source; candidate models explain encoding differences but do not prove a patched library works.
- Is no patch a valid engineering result? Yes, when a proposed fix violates compatibility or lacks a defined protocol policy.
