# Build 30 — reliable public staging contract checks

20 October 2026. Backend candidate tooling only; no production deployment, migration, account move, role grants or frontend changes.

## What changed and why

The old staging smoke accepted any JSON object as health, requested the wrong default health path, and incorrectly required `/openapi.json` to appear inside the schema's own `paths`. It could both misreport health and reject the actual FastAPI contract.

`ops/staging_smoke.py` now takes an HTTPS origin, defaults to `/api` for application routes, and reads `/openapi.json` from the origin root. Health requires the actual `status=healthy` and `service=samvaad-sathi-backend` contract. The schema must be OpenAPI 3, with GET/200 contracts for health and transcription status. The transcription operation is inspected in the schema only; no student attempt is requested or mutated.

Inputs reject credentials, query strings/fragments, paths, ambiguous prefixes, malformed/zero ports and whitespace/backslashes before requests. Redirects are rejected before following. Requests carry a recognizable user agent, use a 15-second socket timeout and read at most 8 MiB plus one sentinel byte. Unexpected content types, malformed JSON, oversized responses, HTTP and network errors fail with bounded diagnostics rather than response bodies or arbitrary exception details.

Optional JSON evidence records UTC time, validated target origin, API prefix/path count and outcome; it explicitly records authenticated acceptance as `not_checked`. Invalid target values are not echoed into evidence. An unwritable evidence destination fails the command even if the check itself passed.

## Execution

From the backend checkout, after an isolated staging deployment with public API docs enabled:

```sh
python ops/staging_smoke.py https://staging.example.com --report /tmp/samvaad-public-smoke.json
```

Use `--api-prefix /v2/api` only when that is the deployed routing contract; `--api-prefix ''` supports an explicitly unprefixed app. The base argument must remain the origin. For deployments that intentionally disable public docs, this staging contract check fails; do not silently treat missing docs as acceptance.

This tool remains manually invoked. It does not replace or change `ops/release.py`'s hosted health check, trigger deployment, configure a staging environment, or assert that a release is approved. Production workflow health/body verification and a coordinated authenticated acceptance runner remain separate follow-up work.

## Verification

- 42 new public smoke cases passed.
- Together with existing production-release and migration safety cases: **61 passed**.
- Black formatting, Python 3.13 syntax compilation and `git diff --check` passed.
- Local pytest used the existing Python 3.14.8 environment. Production/CI target Python 3.12; current candidate CI must independently verify that target. The added tool uses standard-library APIs available in Python 3.12.
- Existing backend PR 149 quality check passed before this build; it is not evidence for the new commit.

Tests cover correct root docs/API prefixes, health identity/status, malformed schema and wrong/missing operations, credential-bearing/ambiguous targets rejected before requests, bounded body reads, wrong content types, invalid JSON, HTTP failures, redirect refusal, successful and failed evidence, and report write failure. Transport is synthetic; no hosted staging, authenticated API, database, provider, physical-device or load acceptance was performed.

## Remaining delivery gates

Provision/select isolated staging, approve the seven historical staff course assignments, and run authenticated student/staff/Sampark enrollment-revocation flows with real source identities. Voice/provider/device/screen-reader/load acceptance, complete visual review, conductor/media and Jev decisions remain governed by their existing plans. No unseen-device cheating guarantee is introduced.

Build 30 is the fourteenth batch against the post-Build-16 estimate in REMAINING-DELIVERY.md. Counting batches does not close acceptance gates. Next work should connect release health verification to the same explicit service contract and prepare isolated authenticated acceptance, rather than assuming public smoke proves readiness.
