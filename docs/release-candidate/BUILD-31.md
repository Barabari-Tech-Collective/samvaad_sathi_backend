# Build 31 — hosted production health contract gate

20 October 2026. Candidate release tooling only. No production deployment, AWS changes, migrations, grants or frontend changes.

## Change

`ops/release.py` previously accepted any hosted HTTP 200, including HTML or an unrelated service, and followed redirects. It now reuses Build 30's bounded JSON reader and explicit health validator from the sibling `ops/staging_smoke.py` in the tested checkout. Loading uses the sibling's absolute file location and does not depend on the working directory or import search path.

Hosted health requires HTTP 200, `application/json`, and the exact service liveness contract (`status=healthy`, `service=samvaad-sathi-backend`). It rejects redirects and reads at most 64 KiB plus one sentinel byte. The release user agent and 30-second socket timeout remain. Six attempts with five 10-second waits are retained; no wait occurs after the final attempt. Failed health can recover on a later attempt; exhausted checks raise a bounded diagnostic code and cause the existing GitHub deployment status to become failure, never success.

The complete raw HEALTH_URL is validated before AWS checks, deployment creation or deployment execution. HTTPS origins with explicit optional ports and root or configured API-prefix `/health` paths are supported. Credentials, queries/fragments (including empty delimiters), ambiguous paths, invalid ports, whitespace and backslashes fail before network activity. The generic configuration helper strips whitespace, so this setting deliberately bypasses its normalization and validates the original value.

Shared JSON transport also handles truncated HTTP responses and deeply nested JSON parse failures as redacted failures. Other staging behavior and the 8 MiB schema response limit are preserved. Production health does not request OpenAPI and can run with API docs disabled. No new credential, provider or dependency is required.

## Verification

- **96 targeted tests passed**, including **35 new hosted-release cases** and the 42 Build 30 smoke cases.
- Black formatting, Python 3.13 syntax compilation, shell syntax and diff checks passed.
- Importing the release module from an unrelated working directory with Python 3.13 passed without running deployment.
- Local pytest used the existing Python 3.14.8 environment; production and CI use Python 3.12. New-commit CI remains required.

Cases cover raw URL rejection before any AWS/deployment side effects, configurable prefixes/ports, preserved edge-security headers, small response limits, unhealthy and wrong-service objects, HTML/invalid JSON/truncated responses, timeouts, redirects, transient recovery, exact retry/wait limits and the actual release main function's success/failure status ordering. All network, AWS and GitHub writes are synthetic in these tests. No live service or production data was used.

## Rollout and remaining gates

This code changes future release runner behavior when adopted; it has not triggered a release. An incorrect target that used to appear successful will now fail. Configure HEALTH_URL to the canonical direct HTTPS `/health` endpoint returning the existing backend contract; fix redirects or incorrect edge responses rather than weakening the gate.

The health route is static service liveness. It does not establish database connectivity, queue/worker readiness, current revision identity, authenticated authorization or interview-provider availability. Hosted failure marks the deployment failed but does not automatically undo a release that passed the host-local gate; operators must inspect the existing rollback runbook. The EC2 host-local curl gate itself remains unchanged and needs equivalent body validation in a subsequent build.

No change to existing default-branch, AWS destination, backup, migration or source gates. Isolated authenticated staging, seven approved historical staff-course mappings, enrollment revocation, actual provider/device/screen-reader/load acceptance and full visual review remain open. AWS relocation remains deferred.

Build 31 is the fifteenth batch against the post-Build-16 estimate in REMAINING-DELIVERY.md. Batch count is not acceptance evidence. Next: strengthen the host-local health/rollback gate, then prepare coordinated isolated authenticated acceptance.
