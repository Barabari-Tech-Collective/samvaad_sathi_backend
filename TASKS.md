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

### C1. About 15 students can answer at once, platform-wide · `TODO`

**This is the concurrency limit behind "it breaks when several students use it".**

Measured on production:

| | |
|---|---|
| `max_connections` | 20 (Aiven reserves ~3 → **~17 usable**) |
| In use at ordinary load | 17 |
| `BACKEND_SERVER_WORKERS` | 1 |
| `DB_POOL_SIZE` + `DB_POOL_OVERFLOW` | 5 + 10 = **15 peak** |
| arq worker | opens no database pool (HTTP to Barabari only) |

The ceiling alone would be survivable. What makes it bite is **how long each
connection is held**:

1. `get_async_session` opens a session for the whole request.
2. `get_current_user` queries on every authenticated request, so SQLAlchemy
   checks a connection out of the pool immediately.
3. `POST /audio/transcribe` then runs Whisper **synchronously** — deliberately,
   because the interview cannot proceed without the transcript — and may follow
   it with an LLM follow-up generation.
4. The session is not closed until the request ends, so that connection is
   pinned for the entire duration of both AI calls.

So a connection is held for seconds to tens of seconds per answer upload, and
there are only ~15. The 16th concurrent upload waits on the pool and then fails
once `pool_timeout` (`DB_TIMEOUT`) elapses.

A campus drive with 50 students answering together gets roughly 15 served and
the rest timing out, intermittently, with no clear error. That matches the
reported symptoms precisely.

Fix options, cheapest first:
- Raise the Aiven connection limit — buys headroom, fixes nothing structural.
- **Stop holding a connection across external AI calls**: use short-lived
  sessions around each database operation instead of one request-long session.
  Contained, and removes the bottleneck rather than widening it.
- Move transcription to the arq worker, return `202`, poll for status. The
  durable fix, and already the audit's recommendation. Largest change.

Still to confirm: production's `DB_TIMEOUT`, which sets how long the 16th
request waits before failing.

### C2. Production's Alembic revision did not exist · `VERIFIED PROD` (2026-10-12)
`alembic_version = 7ddc54281fe2`. That revision is **not among the 46** in the
chain, so `alembic upgrade head` against production fails immediately with
"Can't locate revision identified by '7ddc54281fe2'". Its schema came from
`create_all` plus hand-written `ALTER` statements; the version row is a fossil.

**Verified 2026-10-12:** production's schema matches the models exactly, except
the three `question_attempt.transcription_*` columns added in this branch.
Stamping is therefore safe — there are no unapplied migrations that stamping
would wrongly mark as done.

Fix: set `alembic_version` to `7af582260700`, the head *before* this branch's
migration. `alembic upgrade head` then applies only `a1c4e7b92f08`.

A single-row UPDATE, no DDL, trivially reversible — record `7ddc54281fe2`
first. Runtime-neutral: startup only checks that the table exists, never its
value.

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

## Option 3 — move transcription to the worker (fixes C1)

Rolled out expand/contract: the backend serves both shapes, the frontend
switches, then the old path is removed. Three independently reversible deploys
instead of one flag-day — this is what replaces having a staging environment.

| # | Task | Status |
|---|---|---|
| O3.1 | `transcription_status` / `_error` / `_updated_at` on `question_attempt`, with backfill | `LOCAL` |
| O3.2 | Migration `a1c4e7b92f08`; chain still single-headed at 46 | `LOCAL` |
| O3.3 | Idempotent startup patch for the new columns (needed while C2 blocks migrating production) | `LOCAL` |
| O3.4 | `GET /audio/attempts/{id}/transcription` status endpoint, owner-scoped, 14 tests | `LOCAL` |
| O3.0 | **Store answer audio durably before the request returns** | `TODO` — blocks O3.5 |
| O3.4a | Worker connection pool, sized separately from the API's | `LOCAL` |
| O3.5 | arq task that performs the transcription | `TODO` — blocked by O3.0 |
| O3.6 | `POST /audio/transcribe` returns `202` when the client opts in; synchronous by default | `TODO` |
| O3.7 | Frontend polls to a terminal status | `TODO` |
| O3.8 | Sweep for rows stalled in `processing` (a killed worker must not strand a student) | `TODO` |
| O3.9 | Remove the synchronous path once no client uses it | `TODO` |

### O3.0 — answer audio is never stored (discovered 2026-10-12)

`save_audio_file` writes a `tempfile.NamedTemporaryFile`; `cleanup_temp_audio_file`
deletes it when the request ends. The `audio_url` column holds a generated name
like `qa_42_abc123.wav` that points at nothing.

A background worker therefore has nothing to fetch, and option 3 cannot work
until this changes.

It matters well beyond option 3. Today, if transcription fails the recording is
gone permanently, an answer can never be re-transcribed after a provider
outage, and a disputed score cannot be checked against what the student
actually said.

`src/services/s3_service.py` already provides `upload_audio_to_s3` and
`get_presigned_url`, and is in production use for resumes and TTS audio — it is
simply not wired into the answer path. So the change is contained.

**Needs a product decision first:** durably storing student voice recordings is
a privacy question, not only a technical one. Retention period, encryption at
rest, consent wording, and deletion on request all have to be settled before
this ships.

**Connection budget.** The worker previously opened no database pool; O3.4a
gives it its own, sized for concurrent jobs rather than concurrent requests,
defaulting to a peak of 5. The API's 15 should come down correspondingly — once
connections are no longer held across AI calls it does not need that many —
keeping both totals under the ceiling of 20 with room for migrations and an
admin session.

**Open design questions** (O3.7): does Next unlock on *accepted* or
*transcribed*; where does a follow-up question go when it arrives after the
student has moved on; what happens when transcription fails after the upload
already succeeded; and may a student submit while attempts are still processing.

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
| 2026-10-12 | Stamped `alembic_version` `7ddc54281fe2` → `7af582260700` after confirming production's schema matches the models. Single-row UPDATE, no DDL, no code deployed. | **Success.** Verified by reading the value back. Migrations can run against production again. Revert with `UPDATE alembic_version SET version_num = '7ddc54281fe2';` |
