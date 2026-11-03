# Build 19 — worker claim ownership and late-result fencing

Implemented 20 October 2026; backend release candidate only. Production remains unchanged.

## What changed

Every transcription execution, including an ARQ redelivery, must atomically claim a pending audio answer. A queue retry counter no longer authorizes inheriting an existing processing claim. The worker captures the checksum, answer version and processing timestamp returned by that claim. Transcript saves, pending resets, failures, completion and follow-up resolution match that exact claim in their database update.

Persisting the transcript keeps the claim timestamp unchanged while follow-up work runs. Once a retry/recovery reservation, replacement recording, deletion or terminal result changes the claim, old writes return superseded. An old provider failure cannot reset a newer worker to pending, fail it, or schedule a retry after losing ownership. This also covers reuse of the same audio bytes with another answer version.

Follow-up generation receives the claim, verifies it before starting and rechecks the locked, refreshed row after the model call. A late model result is discarded if the answer or claim changed. Early/no-generation dispositions use conditional updates too. Locked reads refresh the ORM identity map to avoid trusting a cached pre-model row. Publication acquires the interview lock before the answer lock, matching uploads/navigation and avoiding an inverted lock order with follow-up insertion. Audio replacement now locks and refreshes the current answer so concurrent saves advance from the current version.

The upload endpoint also reserves observed failed work atomically and fences queue-failure writes to the pending reservation. It rereads durable state after submission, so enqueue success/failure cannot invent a pending/failed status over worker progress. A replaced recording returns a conflict requiring the current checkpoint; deleted evidence returns not found.

## Recovery behavior

A handled transient failure releases only its own claim to pending, then uses the existing bounded ARQ retry/backoff. A killed worker leaves a processing claim; redelivery does not steal it. The existing sweep can reclaim it after the ten-minute stall threshold (plus scheduler delay). Saved audio and any durable transcript remain available. A recovered worker can reuse a durable transcript rather than repeat transcription.

## Verification

Final results are recorded in the local blueprint verification/revision-19-checks.json. Tests use an isolated local PostgreSQL database and simulated storage/provider boundaries. They cover competing claims, changed claim/version/checksum, terminal state, deletion, late success/retry/exhaustion, queue redelivery and cached-row follow-up races, concurrent upload lock order, actual retry continuation, durable transcript reuse and serialized replacement versions. Final full backend suite: **1,008 passed** (599 warnings), including **37 new PostgreSQL cases** and **four new upload unit regressions**. `git diff --check` passed. No frontend checks were rerun because frontend code is unchanged.

No schema migration, frontend or central-service change. No live queue, production data, cloud-resource or score change.

## Release limits

This prevents stale database writes; it does not guarantee exactly-once provider calls. An old provider call may still consume resources before its result is rejected. Existing queued jobs remain compatible with the task arguments. API and worker candidates must roll out together after old workers drain or stop; an old worker does not enforce these new predicates. Verify real Redis/ARQ worker termination, retry delivery, scheduler recovery, provider timeout behavior and load in staging before production. No live acceptance is claimed from simulated tests. The ten-minute recovery delay is deliberate and should be validated against measured latency.

Build 19 is the third batch against the post-Build-16 estimate in REMAINING-DELIVERY.md. Existing authenticated/device/load, role-scope approval and rollout gates remain open; AWS relocation stays deferred.
