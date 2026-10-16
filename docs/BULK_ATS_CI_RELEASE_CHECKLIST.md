# Bulk ATS CI/CD release checklist

This checklist covers the current Bulk ATS review-workflow, insights, evidence,
rubric-version, and candidate-merge changes. It is intentionally a release
gate: do not deploy if any required check is red.

## Changes in this release

- Reviewer assignment, filtering, bulk decisions, audit history, and batch creator visibility.
- Candidate comparison, calibration summary, cutoff simulation, rank explanation, interview kit, and interview outcomes.
- Evidence metadata with source, section, page, and line ranges when available.
- Rubric snapshots and version creation.
- Canonical candidate merge with the duplicate retained in audit history.
- Explicit run creator display name/email, while retaining the authenticated actor for audit and access control.
- Four forward-only migrations chained from the existing Bulk ATS head.

## CI gates

1. Frontend `quality`: locked install, Bulk ATS Biome checks, TypeScript, Bulk ATS compatibility tests, and production build.
2. Frontend `security`: production dependency audit has no critical vulnerabilities.
3. Backend `quality`: Python compilation, migration safety, empty-database schema test, complete test suite, and release-script syntax checks.
4. Inspect uploaded backend JUnit results even when CI is green; a green workflow with a skipped test is not sufficient.

## Migration order

Run from `samvaad_sathi_backend/backend/backend` after taking a database backup:

```bash
alembic upgrade head
```

The expected new chain is:

```text
e1f2a3b4c5d6
  -> c3d4e5f6a7b8  review workflow
  -> d4e5f6a7b8c9  rubric snapshots
  -> e5f6a7b8c9d0  candidate merge
  -> f6a7b8c9d0e1  creator display details
```

Verify the applied head with `alembic current` and confirm it is
`f6a7b8c9d0e1` (or a later release head if another migration was added).

## Staging acceptance

- Create a batch with a role, optional JD, intent, and skills.
- Upload one file, 100 files, a duplicate, a long filename, a malformed file, and a partial-failure batch.
- Confirm terminal batch status, retries, rerun screening, reviewer assignment, and bulk decisions.
- Open a candidate and verify score explanations, evidence links, project/profile links, notes, interview kit, and feedback generation.
- Create a rubric version and verify the version is immutable for already-screened candidates.
- Merge a duplicate and verify the canonical candidate remains visible and audit history is preserved.
- Exercise keyboard navigation and 320/375/768px layouts.

## Production deploy and rollback

- Record the database backup reference in the release workflow input.
- Deploy backend and frontend only after staging acceptance and green CI.
- Run the production health check, then smoke-test batch creation, candidate detail, rerun, feedback, and export.
- Monitor error rate, API latency, queue depth, AI failure/retry counts, and database migration logs for at least 15 minutes.
- Roll back the application release if the health check fails, candidate processing is stuck, or error rate exceeds the existing service threshold. Do not downgrade the database automatically; investigate and use a forward fix unless the migration owner approves a tested rollback.
