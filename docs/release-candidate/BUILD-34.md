# Build 34 — environment selection and real local source integration

20 October 2026. No production deployment, role grants, cloud provisioning or migration of production data.

## Environment fixes

The staging template set STAGING while the selector only recognized STAGE and silently used production for unknown values. The factory now accepts explicit DEV/DEVELOPMENT, STAGE/STAGING and PROD/PRODUCTION aliases, canonicalizes before constructing Pydantic settings, and rejects unknown values. Missing ENVIRONMENT still retains the existing DEV default. Staging debug defaults off.

The retained Render template now uses /api/health, STAGE, the actual AUTH_SERVICE_BASE_URL key, central identity and Sampark enrollment configuration, explicit error/docs exposure flags and all required settings keys. Operator-supplied secrets remain sync:false. This does not create a Render deployment or prove current Render plan compatibility; the template remains a configuration reference until an actual hosting choice/review. Backend API/worker services are still needed for a complete deployment.

## Executed local integration

New opt-in ops/acceptance/run_local.py starts a newly initialized PostgreSQL cluster and isolated Redis on selected loopback ports. It migrates actual central-auth, Sampark and Samvaad schemas, seeds synthetic records, creates a temporary trusted local TLS certificate and runs actual central password login, identity bridge, Sampark snapshot and full product API code. The central routers use a fixture Express mount rather than the production gateway/index. Broker, provider, notification and OAuth paths are outside this run.

Child environments are explicitly built; saved dotenv files are disabled only in fixture entrypoints. The runner uses its own databases and fixture keys, not production data or credentials. Services/databases stop in finally, and temporary TLS keys/certificates/manifest are removed. Private fixture data and diagnostic logs remain for inspection; force-killing the harness can bypass cleanup and needs manual process-specific recovery. See ops/acceptance/README.md.

Sixteen scenarios passed: central password sign-in; actual Build 33 staff runner across two accounts; positive/negative course boundaries; college/consent record exclusion; source enrollment revoke/restore; personal feature revocation; consent withdraw/restore; stale enrollment denial/fresh recovery; staff membership revoke/restore; central identity active-state revoke/restore with the same token; and central product entitlement revoke/restore with the same token.

## Verification

- Complete backend suite: **1,274 passed**, 614 existing warnings, using disposable local PostgreSQL 15.
- Environment/SSO targeted subset: **27 passed**, including 19 new selection/default tests.
- Real local cross-service acceptance: **16 scenarios passed**, PostgreSQL 15.17, real HTTP/TLS/controllers/SQL and synthetic records.
- Python formatting/compilation, node fixture syntax, YAML parsing and diff checks passed.
- Local Python is 3.14.8; production/CI use 3.12. GitHub CI must separately verify the new commit.

The first real integration report identified a dirty product worktree while testing these changes and central commit ae7c787c017ca44f7766b7551aee1c26a3809fcf. Do not treat that report as exact-commit hosted acceptance. Reusable reports record source revisions and worktree state.

## Operational findings / remaining gates

AWS login renewed under the user's prior authorization; read-only STS/EC2/SSM inspection confirmed account 351405418851. The visible instances in ap-south-1 serve production workloads; no separate staging host or GitHub staging environment was found. No test workload was placed on those hosts and no new hosting cost was incurred. A hosting-budget preference was requested while independent implementation continues.

Local source integration is now executed, replacing a significant part of the earlier synthetic-only evidence. It does not close hosted PostgreSQL 17 migration/restore, full deployed central/gateway/broker sync, student voice/receipt/browser/device/provider/load, seven approved historical staff mappings, visual acceptance or rollout. AWS relocation and unknown historical role repair remain deferred.

Next implementation: responsive interview canvas and practice companion presentation tied to real media/answer state, while hosted staging remains separately constrained by available resources.
