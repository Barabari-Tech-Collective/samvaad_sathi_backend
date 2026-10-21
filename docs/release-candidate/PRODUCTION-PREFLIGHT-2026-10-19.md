# Production backup and release preflight — 19 October 2026

## Backup completed

AWS account verified: `351405418851`; region: `ap-south-1`. Aiven PostgreSQL source version: 17.11. Source migration revision: `c5e6f7a8b9d0`. No production schema or application deployment was performed.

Backup created at `20261019T103929Z`; verified at `2026-10-19T10:43:11.106434+00:00`.

- Reference: `s3://samvaad-private-backups-351405418851-ap-south-1/database/20261019T103929Z/database.dump?versionId=r6XX1EaPlkfo9Zst2lxsSnwtm5ZTqUlU`
- Archive size: 7816565 bytes.
- SHA-256: `f3c8ad47074a08b01ea475da2817eb748658ce97b866bca50ded223a6ba892bf`.
- Restored successfully into isolated PostgreSQL 17: **33 tables**.
- Encrypted S3 object read back and SHA-256 verified; object version recorded above.
- Bucket blocks all public access, disables ACLs, requires HTTPS and has AES256 server-side encryption and versioning enabled.
- Temporary host dump removed after durable-copy verification; temporary restore container removed.

This is a PostgreSQL custom-format logical backup, not an Aiven provider snapshot or point-in-time recovery record. Database ownership/ACLs and cluster-global role definitions are excluded; existing deployment roles must be available when restoring. Existing recording/resume S3 objects are outside this database archive. The backup has not been made public or downloaded as a local data file.

## Deployment preflight

- All four candidate PRs' final CI checks passed: backend #149, dashboard #95, student #57, Sampark #18.
- The production SSM release document is installed and the host is online. API and ARQ worker use the expected systemd virtualenv executables.
- Dashboard Amplify main auto-build changed from enabled to disabled to honor coordinated gated releases. Student Amplify main auto-build was already disabled.
- No backend staging GitHub environment is configured.
- Current production has **seven active staff memberships**, all requiring academic course bootstrap for the new scoped workspace.

## Release hold

Do not merge/deploy this candidate while the seven memberships lack approved course assignments and equivalent scoped staff workflows remain incomplete. The new guards would otherwise block existing staff reporting workflows. The local `STAFF-COURSE-BOOTSTRAP.csv` records the current memberships and existing college boundaries; intended course IDs and approval reasons are deliberately blank. Staff details are kept in the local worksheet, not committed here.

Next required work: confirm course mapping, finish scoped workflow parity, and run authenticated acceptance against a suitable isolated target before production rollout. Backup creation authorization is complete; AWS shifting and historical role relabeling remain deferred.

S3 storage/request charges apply. Establish an explicit retention/restore-drill policy; access logging, CloudTrail object data events and CloudWatch storage/request metrics are recommended operational follow-up and were not newly enabled by this backup step.
