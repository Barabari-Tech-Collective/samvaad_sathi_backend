# Build 33 — executable scoped-staff staging baseline

20 October 2026. Candidate tooling only; no live acceptance, deployment, infrastructure changes or role assignments.

## Implemented

`ops/staff_acceptance.py` adds an executable GET-only baseline for approved, centrally authenticated, non-admin facilitator/placement staging accounts with enabled course reads. Its strict versioned manifest declares the isolated origin, expected local user IDs, exact course sets, exact personal permissions and forbidden courses. Tokens come only from named environment variables. Unknown fields, credentials/query-bearing origins, duplicate identities/tokens, ambiguous IDs, overlapping/missing scopes and invalid policies fail before requests. Every actor is validated up front and the total request budget is capped at 200.

For each actor, the runner checks central identity provenance, expected person, no administrator/global bypass, exact course assignments and exact personal permission policy. It performs a bounded assigned-course roster read and requires explicit 403 on each forbidden course. Authentication failures, 404, 5xx, redirects or network errors cannot count as authorization denial. Identity/scope/permission snapshots are checked again at the end; expiry, version or relationship changes invalidate the run. This bounds common stale-session errors but is not an atomic snapshot.

All requests use fixed `/api/v2` route families, GET, a recognizable user agent, redirect refusal, direct transport without environment proxies, a 15-second socket timeout and 1 MiB response limit. No token refresh, account creation, role mutation or deployment action exists in the runner. Shared JSON transport now accepts a bearer token without exposing it in errors and closes HTTP error responses.

The required JSON report contains UTC time, validated target origin, manifest SHA-256, actor labels and bounded outcome codes. It excludes tokens, user/course IDs, policies, roster records and response bodies. Manifest input reads are size-bounded and report writes must succeed; the report may not overwrite its manifest. Evidence explicitly marks enrollment revocation and full journeys as not checked.

`ops/STAFF-ACCEPTANCE.md` provides the preparation/interpretation runbook. `ops/examples/staff-acceptance.json` contains an intentionally invalid example hostname and placeholder identities/course IDs. It must be replaced with approved staging settings. CI syntax checks include the new runner. No workflow automatically sends authenticated requests.

## Verification

52 new synthetic runner cases cover positive contracts, strict preflight, missing/reused tokens, wrong identity/provenance, administrator/global bypass, scope mismatch, absent/changed permissions, policy version, positive course denial, wrong roster context, incorrect foreign 200/401/404/5xx responses, access changes during a run, request budgets, GET bearer transport, redirects/network errors, redacted reports, manifest hashing and report write/overwrite protection.

Together with existing public/hosted/local/migration safety checks, 177 targeted cases pass. Black, Python 3.13 syntax compilation, Bash syntax and diff checks pass. Local pytest uses the existing Python 3.14.8 environment; new-commit Python 3.12 CI remains independently required. The preceding Build 32 backend candidate quality check passed before this build; that is not a new-commit result.

All identity/API responses in these tests are synthetic. No approved isolated staging origin and test-account tokens were supplied or discovered as part of this build, so no real authenticated acceptance was performed.

## Remaining gates

The `isolated_staging` label is an operator declaration, not proof of resource isolation. GET authentication may bind or synchronize identities/enrollments, so the tool must only run against independently verified staging resources. It must never be pointed at production to compensate for missing staging. The seven unresolved production staff mappings remain untouched.

This profile intentionally excludes students, admins/super-admins, fully revoked personal features, unassigned staff and mixed-role fixtures without positive coaching-course reads. Assigned-course roster shape does not prove record-level college/cohort/consent filtering. Additional known-record fixtures must prove those boundaries, student enrollment freshness/revocation, write capabilities, exact question/audio/receipt journeys, browser sessions and real providers/devices/load. Static health gates and this runner alone do not authorize production.

Build 33 is the seventeenth batch against the post-Build-16 estimate; that count is not pilot acceptance. Next: verify/configure isolated staging and execute this baseline with approved accounts, then add known-record/student revocation scenarios and fix actual findings. AWS relocation and historical mapping repair remain deferred.
