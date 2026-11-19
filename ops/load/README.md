# Load test: students and staff at once

Runs against a **local, disposable** backend only. Never point it at staging or production.

## Setup

1. PostgreSQL with `max_connections=20` (the production limit) and a database `samvaad_load`
   owned by `ci` (password `ci-only-password`). Run `alembic upgrade head`, then
   `psql -f ops/load/seed.sql` (2,000 students, 20,000 interviews, 100,000 answers).
2. Redis on port 6380.
3. `source ops/load/env.sh` and start, from `backend/backend`:
   - API: `uvicorn src.main:backend_app --host 127.0.0.1 --port 8088 --workers 2`
     with `DB_POOL_SIZE=3 DB_POOL_OVERFLOW=3` (2 processes x 6 = 12 connections)
   - Worker: `arq src.worker.main.WorkerSettings` with `WORKER_DB_POOL_SIZE=2 WORKER_DB_POOL_OVERFLOW=3`
4. `python ops/load/run_load.py --students 100 --staff 100 --minutes 3`

Students open Home, poll a transcript every 1.5 s for 30 s, then think 20-40 s. Staff open the
Overview (all of its requests in parallel), then think 15-45 s. AI providers are not called.

## Results, 19 Nov 2026 (2-core sandbox shared by API, worker, Postgres, Redis and the load generator)

| Setup | Staff Overview p50 / p95 | Student Home p50 / p95 | Transcript poll p50 / p95 | Errors |
| --- | --- | --- | --- | --- |
| Before: 1 process, no shared cache | 18.5 s / 20.7 s | 3.4 s / 9.3 s | 0.2 s / 2.9 s | 16% of staff requests 503 |
| After: 2 processes, shared cache refreshed by the worker, lighter queries | 0.4 s / 13 s | 0.7 s / 8.6 s | 0.1 s / 1.2 s | none |
| 100 students alone, after | n/a | 0.07 s / 0.31 s | 0.01 s / 0.05 s | none |

The p95 tail comes from the first minute, when 100 staff open the Overview at the same second
on a cold cache, and from the 2 shared cores. Each request costs about 7 ms of CPU in this stack,
so one core serves about 140 requests a second.

## Results, 19 Nov 2026 evening: 300 students and 100 staff (same 2-core sandbox, 12 API connections)

| Run | Staff Overview p50 / p95 | Student Home p50 / p95 | Transcript poll p50 / p95 | Errors |
| --- | --- | --- | --- | --- |
| Before the sign-in race fix | 0.48 s / 12.1 s | 0.50 s / 11.4 s | 0.35 s / 4.5 s | 77 x 500 (two first requests created the same user), ~1,800 x 503 |
| After the fix | 0.77 s / 10.7 s | 0.73 s / 10.9 s | 0.24 s / 2.7 s | no 500s; ~1,400 x 503 (5% of 26,628 requests) |

Every 503 is a request that waited 10 s for one of the 12 API connections. At 300 students
the limit is Postgres's 20 connections, not the code: put PgBouncer (transaction mode) in
front and set `DB_BEHIND_PGBOUNCER=true`, which turns off asyncpg's statement cache as that
mode needs. Then raise the API pools and run this test again.
