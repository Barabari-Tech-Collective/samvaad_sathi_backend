# Cross-account migration: Samvaad Saathi

21 October 2026. Resumed by the user. Keep current public domains and Mumbai (`ap-south-1`). Include application database **and central authentication services/data**. No separate hosted staging environment. Destination account/profile are still required. This preparation has not moved production.

## Start with discovery

From the backend repository:

```sh
python3 -m unittest discover -s ops -p test_aws_migration.py
python3 ops/aws_migration.py validate-manifest ops/examples/aws-migration.json
python3 ops/aws_migration.py inventory --profile samvaad-login --expected-account 351405418851 --region ap-south-1 --output /tmp/samvaad-source-inventory.json
```

The example intentionally fails validation until the destination identity is supplied. Copy it to a private working file before filling verified identifiers and secret references. Do not commit inventories containing sensitive infrastructure identifiers. Inventory files are created with mode `0600`, never overwritten, and should be stored in controlled migration evidence storage. No secret values, application records, password hashes or object bodies belong in reports.

The tool only runs fixed metadata discovery commands, verifies STS identity first, removes ambient credential/endpoint overrides, keeps CLI pagination enabled, and reports inaccessible sections separately from empty results. Exit `0` means these discovery calls completed, `2` means partial discovery, and `1` means an identity/input/tool failure. A successful discovery or manifest validation **never means cutover ready**. The CLI profile must already be authenticated; the tool never logs in automatically.

This is a first inventory pass. Account-wide S3/Route 53 discovery includes unrelated resources; explicitly assign each resource `move`, `retain`, `shared` or `unused` before changes. Mumbai discovery does not cover global CloudFront distributions, IAM/OIDC, billing, edge certificates, other regions, complete networking rules, Amplify branches/domains/environment references or service configuration. Follow-up inventory must cover those dependencies without dumping secrets. Aiven and central auth may be external to this AWS account.

## Execution order and gates

| Step | Work | Required evidence before advancing |
|---|---|---|
| 1. Source and target identity | Verify both accounts and inventory deployed release, AWS resources, external DB/auth, shared Sampark consumers and domain ownership. | Expected-account STS checks; reviewed resource mapping; owners; destination access. |
| 2. Target infrastructure | Recreate the verified topology using repeatable infrastructure. Choose DB hosting only after inventory; avoid combining relocation with a new auth provider or engine change. | Reviewed plan, resource/cost scope, networking, runtime roles, secret references, TLS, backups and alarms. No public traffic switch. |
| 3. Release path | Recreate exact-repository GitHub OIDC trust, account/region guards, SSM release document, API/worker services and Amplify releases. | Known deployed commits recorded; wrong-account negative checks; target API and workers healthy. No unfinished local Build 39 changes. |
| 4. Database and auth rehearsal | Restore a fresh verified application/auth backup into the intended destination with production traffic disabled. Preserve IDs, compatible hashes, verification, grants, consent, sequences, migration heads and shared identities. | Restore duration; row/reference reconciliation; staff/student login, reset, refresh/logout, revocations, SSO callback replay denial; shared consumers verified. |
| 5. Storage and queues | Reconcile bucket keys/metadata, retained versions/delete markers, encryption/ownership, private access, stored absolute URLs, ARQ jobs and auth revocations. | Counts/bytes plus checksums or documented sampled verification; private retrieval denial; historical evidence retrieval; idempotency and revocation checks. No `sync --delete`. |
| 6. Destination acceptance | Use restricted access to the intended new production deployment; no additional hosted staging. Validate TLS, domain ownership/certificates, CORS/cookies, voice save/submit, practice/assessment separation, staff course scope, provider callbacks and workers. | Meaningful user journeys and failure/recovery evidence, including migrated records; health alone is insufficient. |
| 7. Timed cutover | Agree window; pause new attempts, drain supported sessions, fence source writes and old workers/consumers, final backups/deltas, reconcile, switch domains and enable destination writers. | One authoritative writer; fresh restore-capable backup; named go/no-go owner; measured rollback procedure. |
| 8. Observe and retire | Observe auth, submissions, queues, evidence retrieval, error/latency and cost against agreed thresholds; test target backup restore. | Acceptance and retention window complete. Revoke temporary transfer permissions. Old-resource deletion requires a separate explicit decision. |

## Failure and recovery rules

- An account mismatch, denied inventory call, unknown shared auth consumer or missing backup blocks the affected stage. Never infer absence from AccessDenied.
- Existing domains remain; DNS/certificate and Amplify ownership constraints must be tested for the actual services before the cutover window. Retaining a domain name does not transfer its hosted zone, registrar or certificate automatically.
- A stale backup is rehearsal evidence only. The verified 19 October backup does not replace final application **and auth** backups. Existing S3 objects are outside a PostgreSQL archive.
- A source write fence must cover API, workers, scheduled jobs, auth events and integrations. Handle active interviews before fencing; do not silently discard saved answers or acknowledged jobs.
- Blacklists/revocations are security state. Migrate supported state or explicitly invalidate sessions across all consumers and require fresh login. Never start an empty revocation store while accepting old tokens.
- Preserve signing-key/session policy deliberately. Do not rotate keys accidentally, rematch identities only by email, or run imports that duplicate users.
- Reconcile asynchronous work with stable IDs; expired single-use SSO codes should require fresh login, not replay. Test reconnect and provider callbacks around the fence.
- Rollback before destination writes can restore traffic to the intact source after checking writers/sessions. **After destination writes, DNS rollback alone risks data loss:** fence destination and reconcile its delta or repair forward using the rehearsed procedure.
- Stop for identity duplication, privilege/scope drift, resurrected revoked access, missing acknowledged evidence, divergent writers or unreconciled jobs. Choose numerical availability/latency thresholds and an observation window from actual baseline/rehearsal evidence.

## Current state

Local inventory tooling and offline tests are available. Source account `351405418851` was verified on 19 October; a fresh check requires renewed login. Destination identity is pending. No target resources, data copies, DNS changes or cutover have been executed in this migration work. Provisioning templates and transfer commands will be generated from verified mappings, rather than guessed infrastructure.
