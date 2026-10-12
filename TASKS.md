# Samvaad Saathi — Task Ledger

Source of truth for what is done, what is verified, and what is still broken.
Branch: `Manager-Arma-new-samvaad-10` (backend, frontend, dashboard).

**Update this file in the same commit as the work it describes.** A task is only
`VERIFIED PROD` once someone has exercised it against production and said so.

| Status | Meaning |
|---|---|
| `TODO` | Not started |
| `WIP` | In progress |
| `LOCAL` | Written, tests pass locally, never run anywhere else |
| `CI` | Proven by CI on a clean checkout |
| `VERIFIED PROD` | Exercised against production and confirmed working |
| `BROKE` | Shipped and caused a problem — keep the record, do not delete |

No staging environment exists. Local and CI are the only gates before production.

---

## Production facts (verified 2026-10-12)

| Thing | Value |
|---|---|
| Database | Aiven managed PostgreSQL **17.11** (not on the EC2 host) |
| App host | EC2, Ubuntu, Python **3.12.3** |
| App layout | In-place git checkout at `~/apps/samvaad_sathi_backend` |
| Env file | `~/apps/samvaad_sathi_backend/backend/.env` |
| Services | `samvaad_sathi_backend.service`, `samvaad-worker.service` (systemd) |
| Frontend | AWS Amplify · Backend: EC2 · Dashboard: part of Sampark Saathi Amplify |

---

## Critical findings — unresolved

### C1. Database connection ceiling is nearly exhausted · `TODO`
`max_connections = 20`, **17 already in use** at ordinary load. Three spare.

The API pool and the arq worker pool both point at the same instance. A modest
burst of concurrent interviews exhausts it and every request fails. This is the
most likely explanation for intermittent, unreproducible production breakage,
and it will not survive a campus drive.

Next: read `DB_POOL_SIZE`, `DB_MAX_POOL_CON`, `DB_POOL_OVERFLOW` from production
and compare the total against 20. Then either size the pools to fit or move the
Aiven plan up.

### C2. Production's Alembic revision does not exist · `TODO`
`alembic_version = 7ddc54281fe2`. That revision is **not among the 45** in the
chain. `alembic upgrade head` against production fails immediately with
"Can't locate revision identified by '7ddc54281fe2'".

So production cannot currently be migrated at all. Its schema came from
`create_all` plus hand-written `ALTER` statements; the version row is a fossil.

Must be resolved before any migration-based deploy. Likely path: confirm
production's schema matches the model, then `alembic stamp` it to the correct
revision. Needs care — stamping the wrong revision silently skips real
migrations.

### C3. 796 duplicate question attempts in production · `TODO`
796 distinct `(interview_id, question_id)` groups have more than one row.

This is the non-idempotency defect, measured. It also means **seam 2's unique
constraint cannot simply be added** — the migration would fail on apply. A
reconciliation step must come first: decide which attempt is canonical (most
likely the one with a transcription/analysis, else the newest), preserve it, and
collapse the rest without losing student answers.

### C4. Production secrets duplicated on disk · `TODO`
`.env.save`, `.env.save.1`, `.env.save.2` sit beside `.env` on the EC2 host —
editor backups from hand-editing config in place. Each is a full copy of live
credentials. Diff against `.env` first (one may hold a value the live file lost),
then remove.

### C5. `.env` does not parse as shell · `TODO`
Sourcing it emits `SESSION_SECRET_KEY: command not found` and similar, meaning
it half-loads. The app is unaffected (`python-decouple` parses the file itself),
but systemd `EnvironmentFile=` and any shell-based deploy tooling may silently
miss variables. Confirm the services actually receive every value they expect.

---

## Seam 1 — one authoritative schema mechanism

Today there are three, each hiding the others' gaps: Alembic (could not build
the schema), `create_all(checkfirst=True)` (creates missing tables, never
missing columns), and a hand-written `ALTER` block that grew by one entry per
incident.

| # | Task | Status |
|---|---|---|
| 1.1 | `interview.difficulty` added before `alter_column` so the chain runs from scratch | `LOCAL` |
| 1.2 | CI gate: empty PostgreSQL + `alembic upgrade head` must reproduce the model schema | `LOCAL` — never executed |
| 1.3 | Startup reports any column the models declare and the database lacks | `LOCAL` |
| 1.4 | Patch `interview.difficulty` / `job_profile_id` idempotently at startup | `LOCAL` |
| 1.5 | Resolve C2 so production can be migrated | `TODO` |
| 1.6 | Retire `create_all` fallback and the hand-patch block once 1.2 is green and production is consistent | `TODO` |

1.6 is deliberately last. The patches are what keep production running today.

## Seam 2 — server-owned interview state with idempotency

Root cause of frozen buttons and changing questions.

| # | Task | Status |
|---|---|---|
| 2.1 | Reconcile the 796 duplicate attempts (C3) without losing answers | `TODO` |
| 2.2 | Unique constraint on `(interview_id, question_id, attempt_number)` | `TODO` — blocked by 2.1 |
| 2.3 | Idempotent start/resume attempt command keyed by client command id | `TODO` |
| 2.4 | Immutable published question snapshot pinned at interview creation | `TODO` |
| 2.5 | Stop `localStorage` being authoritative for question content | `TODO` |
| 2.6 | Server-owned interview/attempt state machine | `TODO` |

Client-side half already landed: answer is only accepted on `saved: true` for
the matching attempt, recording is retained on failure, and late responses
cannot attach to a question the student has left.

## Seam 3 — CI and deployment that actually run

| # | Task | Status |
|---|---|---|
| 3.1 | CI workflow (compile, full suite, synthetic settings) | `LOCAL` — never executed |
| 3.2 | CI Python pinned to 3.12 to match production | `LOCAL` |
| 3.3 | CI PostgreSQL pinned to 17 to match Aiven | `LOCAL` |
| 3.4 | Push branch so CI runs for the first time | `TODO` |
| 3.5 | Rewrite `ops/ec2-release.sh` for the real layout | `TODO` — blocked, see below |
| 3.6 | Frontend-first deploy ordering (the `audio.py` 503 is a breaking change) | `TODO` |
| 3.7 | Walk one full interview locally — the only pre-production rehearsal available | `TODO` |

### 3.5 blockers and requirements
`ops/ec2-release.sh` assumes `/opt/samvaad/releases/<sha>`, an
`/opt/samvaad/current` symlink, and `/etc/samvaad-release.conf`. None exist —
production is an in-place git checkout. The script aborts on line 9.

Needs `systemctl cat samvaad_sathi_backend.service samvaad-worker.service` to
rewrite correctly. Requirements once rewritten:
- in-place `git fetch` + checkout of a pinned SHA, not release directories
- record the current SHA and roll back to it automatically on health-check failure
- hold a lock so two releases cannot overlap
- migrations run against **Aiven over the network with SSL**, never localhost
- keep the existing refusal to deploy migration changes without an approved procedure

---

## Completed

| Change | Verified how | Status |
|---|---|---|
| Answer save is durable; recording retained and retryable on failure | 19 unit tests + real-Chromium e2e asserting byte-identical WAV on retry | `LOCAL` |
| Footer no longer freezes while the mic prompt is open | 2 regression tests | `LOCAL` |
| Microphone wait cut 20s → 5s, with an accurate message for an unanswered prompt | Unit test pins 4s pending / 5.5s released | `LOCAL` |
| Server errors return CORS headers so the browser can read them | 3 tests; immediately exposed the `difficulty` bug in one round trip | `LOCAL` |
| CORS allowlist: concatenated origin repaired, duplicate removed | AST parse + live preflight against 3 origins | `LOCAL` |
| SSO refuses to serve when `AUTH_SERVICE_BASE_URL` is unset | 8 tests + live 503 | `LOCAL` |
| Dead "Continue" button replaced with a stated error | Rendered href checked | `LOCAL` |
| `is_admin` lint guard no longer blind to nested reads | Meta-test proves detection | `LOCAL` |
| e2e no longer fails on every cold CI run | Verified from a killed server | `LOCAL` |
| `ops/` scripts checked against real settings names | Caught a live bug on first run | `LOCAL` |
| DEV-only local sign-in helper | Used successfully to log in | `VERIFIED LOCAL` |

Suites: backend 245 passed / 2 skipped · frontend 19 passed · e2e 1 passed.

---

## Production deployment log

Nothing from this branch has been deployed. Record every attempt here —
including failures, which are the most useful entries.

| Date | What | Outcome |
|---|---|---|
| — | — | — |
