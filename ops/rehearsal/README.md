# Migration release rehearsal

`ops/rehearsal/rehearse.sh [FROM_REVISION] [DUMP.sql]` runs on a disposable local Postgres
(max_connections=20, like production). It builds the schema at the last released revision,
loads data, then times upgrade to head, downgrade back, and upgrade again, and fails if any
step errors or a row is lost.

Pass a sanitised production dump as the second argument before a real release. Without one
it uses the synthetic load seed (2,000 students, 20,000 interviews, 100,000 answers).

## 19 Nov 2026, synthetic data, d7e8f9a0b1c2 → e2b3c4d5f6a7

| Step | Time |
| --- | --- |
| Upgrade to head | 0.8 s |
| Downgrade to d7e8f9a0b1c2 | 0.8 s |
| Upgrade again | 0.8 s |

No rows lost. A run on a sanitised production copy is still needed before release.
