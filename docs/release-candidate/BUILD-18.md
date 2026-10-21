# Build 18 — atomic saved-answer processing recovery

Implemented 19 October 2026; backend release candidate only, not deployed.

## Problem and fix

Manual “Retry speech feedback” previously checked a state read before its reset write. A concurrent retry or worker could advance that state before the reset, allowing active work to be reset to pending. Queue submission failure could also mark newer work failed. The background sweep had a comparable read/reset race and ignored pending work orphaned before queue submission.

New repository operations reserve exactly the observed saved answer using a conditional database update. The reservation matches attempt ID, owner, checksum, answer version, previous transcription status and previous update timestamp, and requires durable audio. Failed work is eligible immediately. Pending/processing work is eligible only after ten minutes (or a missing legacy timestamp); the worker call timeout is five minutes, leaving a recovery buffer. Fresh pending/processing and completed transcription are not reset. SQL NULL observations are handled explicitly by the generated null predicate.

Only the winning request queues work. Other retry requests return the current owned status, not an invented pending result or a duplicate queue submission. Owner reads refresh the ORM identity map so an older in-memory state does not mask newer worker progress. A newer checksum or answer version, a changed timestamp/status, or another owner cannot claim the observed reservation.

A failed enqueue can mark only its own still-pending reservation failed, using checksum and the reservation timestamp. If a worker has started, completed or a newer answer/state has replaced it, the failure marker does not overwrite that state; the endpoint returns current status. Actual queue outage returns 503 with Retry-After while leaving saved audio and checksum intact. Accepted answers may be reprocessed after an interview has ended or enrollment has changed, consistent with retaining processing already accepted by the server.

The background sweep uses the same owner-scoped conditional reservation, handles stale pending as well as processing work, and scans at most 100 rows in oldest-update/ID order. Its failed-enqueue write is equally fenced. Failed rows await explicit retry rather than being endlessly retried by the sweep. A crash after reservation and before queue submission leaves stale pending work that the sweep can recover later.

Legacy transcription metadata that is not an object is omitted from the status response and uses a safe default language during retry; malformed JSON shape no longer breaks that response. This does not silently manufacture a transcript.

## Validation

Final full backend suite: **967 passed** (599 warnings); all 15 new real PostgreSQL recovery cases passed. `git diff --check` passed. Detailed results are recorded in the local product blueprint’s verification/revision-18-checks.json. New real PostgreSQL cases cover eight simultaneous failed-answer retries, fresh/stale pending and processing, already-transcribed state, wrong owner, checksum/version changes, worker state changes after the browser read, queue outage and later recovery, worker-started/worker-completed enqueue races, simultaneous browser/sweeper reservation and malformed legacy metadata. Existing worker, upload, ownership, migration/schema and interview suites are rerun.

No schema migration, frontend or central-service changes are required. No production data, queue, provider calls, roles, cloud resources or student scores were changed. All new database tests use the disposable local database; queue/provider behavior is simulated. Real Redis/ARQ delivery, provider timeouts, worker termination and production-like load remain authenticated/staging acceptance requirements.

## Limits and next checks

This is an atomic retry/recovery reservation, not a claim of exactly-once external model execution. Redis delivery and external provider execution can fail independently of the database. Existing checksum guards remain, while broader worker-generation fencing and original-upload enqueue/result races still deserve their own fault review. In-flight provider work that outlives the expected timeout cannot be proven stopped by a database timestamp. The ten-minute recovery buffer is deliberate and should be evaluated under measured provider latency before changing it.

Build 18 is the second batch against the post-Build-16 estimate in REMAINING-DELIVERY.md, not a new reset of that estimate. Remaining authenticated/device/load and rollout gates stay open. Production remains unchanged.
