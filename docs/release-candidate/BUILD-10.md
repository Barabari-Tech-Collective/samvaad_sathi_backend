# Build 10 — personal feature permissions and production release candidate

19 October 2026. User authorized GitHub publication and production deployment. Production has not been deployed by this build.

## Personal feature access

Admins and super admins can open **Personal access**, select an active staff account, select individual features and operations, and save a reason. The policy belongs to that individual, not every person with the same role. Removing read removes dependent operations. An explicit empty policy revokes all managed features. Existing NULL policies preserve current role defaults until an administrator deliberately applies a policy.

Managed catalogue: interview authoring (read/create/update/delete/publish/generate); course workspace (read/update); analytics (read); practice coaching, bulk ATS and teams (read/create/update/delete). The catalogue is extensible, but this release does not claim coverage of every Sampark product or hundreds of hypothetical features. Role template creation and permission presets are future work; the existing team-role assignment remains the source of staff responsibilities.

Server dependencies enforce the selected operation across the corresponding router, including alternate template/question endpoints. Dashboard navigation and page guards consume the same saved policy. Creator controls reflect key operation selections. Existing role authority, course/college boundaries, student sharing and administrator-only publication approval still apply. This is a personal restriction layer, not permission to bypass those data boundaries or grant every role every legacy operation. Administrator authority cannot be edited through this screen.

Permission changes lock the target account, compare an expected version, increment it and audit before/after plus actor and reason in the same transaction. No client-supplied target account data grants admin authority. A subsequent protected request checks the current database policy; already loaded browser content cannot be retroactively erased.

Additive migration: `c1e2f3a4b5c6`, following `b0d1e2f3a4b5`. Apply compatible backend before dashboard. Do not deploy frontend page guards against an older backend without the feature-access endpoint.

## Release hardening completed

- Independent dashboard browser suite; student tests no longer depend on a sibling private repository.
- CI provisions the isolated fixed-round database and runs database/HTTP regression tests rather than skipping them.
- Dashboard PostCSS pinned to patched 8.5.31; production npm audit reports zero vulnerabilities.
- Compatible Sampark service dependency patches reduce three critical/one high findings to two critical findings in its old Eureka/request chain; this remains a blocker, not a clean audit.
- Production workflow AWS_EXPECTED_ACCOUNT_ID configured for all three product repositories to 351405418851, matching existing deployment role ARNs. No AWS resources migrated or deployed.

## Local validation

- Complete backend suite with isolated PostgreSQL and migration/schema checks: **850 passed**, 602 warnings.
- Student unit tests: **101 passed**; full ESLint, release lint, TypeScript and production build passed.
- Dashboard: TypeScript, changed product-file Biome and production build passed; **35 Node regression tests** and **4 browser tests** passed.
- Student independent browser suite: **3 passed** (guide plus two recording/recovery cases).
- Enrollment source: **4 tests passed**. See release record for final TypeScript and audit outcome.
- Browser auth and external AI/media are mocked. This is not authenticated production/provider/device/load acceptance.

## Unresolved production gates

1. Verified production database backup/snapshot reference is required by the existing release workflow; none supplied yet.
2. Actual authenticated staging acceptance and coordinated rollout/migration/rollback evidence remain required.
3. Scoped staff workflow parity is incomplete. Legacy guards intentionally block unadapted broad reports; do not deploy guards alone and remove staff workflows without a replacement.
4. Sampark service has two critical dependency advisories without compatible automatic fixes (Eureka/request dependency chain).
5. Human/video rooms, Jev evaluation, adaptive slots, active-session enrollment revocation and the full visual rollout remain unfinished. Neither this build nor the existing prototype proves those features production-ready.

Push reviewed candidate branches and use CI results as release evidence. Do not invent backup references, staging signoff or provider acceptance just to dispatch production.
