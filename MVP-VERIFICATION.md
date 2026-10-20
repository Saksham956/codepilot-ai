# Local MVP verification

The local app runs on http://127.0.0.1:8010. The browser flow loads the frontend, opens the recorded Requests report, and offers the fixed isolated Requests baseline run. JSON report and validated unified-diff downloads are implemented in the browser without retaining source on the server.

The installed regression selection ran 31 tests: 30 passed and one was skipped because this Windows account lacks symbolic-link creation privilege. Selected suites: MVP report API, safe patching, patch validator, clone safety, pinned real-source gate and compatibility assessment.

The live AI branch was exercised using a controlled provider response and temporary repository fixture. Success produced a diff, syntax failure produced no diff, and an empty proposal produced a no-change report. Original source remained unchanged. Cross-origin requests, unexpected hosts, oversized requests, unknown fields and missing source-sharing consent were rejected. Provider/internal error details were not returned to the browser.

Existing real-source evidence contains seven smoke checks plus twelve compatibility cases, all passing against the pinned baseline. A passing Unicode case reproduces the expected exception. No candidate patch or upstream test suite was run.

No real Gemini request was made for this verification. Cross-repository automated tests, production deployment and a fully autonomous repair system remain outside the completed local interface milestone. See README-MVP.md for setup and limitations.
