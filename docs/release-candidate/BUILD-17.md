# Build 17 — active interview course-access checks

Implemented 19 October 2026; candidate only. No deployment or new database migration.

## Delivered

Course-bound active/paused participation is rechecked against a freshly loaded student enrollment snapshot rather than an earlier authenticated-user cache. The shared check distinguishes removed enrollment (`COURSE_ACCESS_REVOKED`) from stale/unverified Sampark state (`COURSE_ACCESS_UNVERIFIED`). Freshness follows the configured source policy; if Sampark sync is not enabled, existing administrator-verified course membership applies. Enabling the production source remains a separate acceptance gate.

Checks cover new-client V1/V2 reuse of an existing interview, begin/resume/navigation/new-attempt/completion paths using the owned-row lock helper, practice lease claim/heartbeat, initial question generation in V1/V2 and non-tech routes, in-progress question listing/supplements, and the audio save preflight/recheck. Audio processing already accepted by the server, retained attempt history and completed submission receipts are not erased or reclassified. Untagged historical/self-practice retains its original contract.

The denial leaves the attempt and saved answers intact; it does not mark cheating, terminate, penalize or automatically restart it. Restored enrollment allows another request under the original lease/deadline rules. Formal deadlines continue and are not extended by this check; support/accommodation policy for restoration after deadline is still a separate requirement. Save-and-exit mutations that use the owned-row lock also require valid course access, while saved history remains retained. No UI claim of server-saved audio is made after a rejected upload.

The student displays a distinct program-team/enrollment message for lease acquisition/renewal failure and disables writing. It can explicitly recheck after restoration. When audio save is denied, the existing buffered recording is kept for retry and the UI explains it has not been saved to the account. Local buffering depends on the existing browser storage/memory support, not a new durability guarantee.

## Verification

The backend suite includes real PostgreSQL scenarios for both practice and assessment denial on begin/resume/audio, a refreshed identity map after revocation, restoration, stale enrollment during lease claim/heartbeat, unchanged stored lease/evidence and retained completed receipts. Unit cases cover legacy/history behavior and missing-user fail-closed handling. Student tests cover distinct enrollment messages, write disabling and recovery; a real MediaRecorder browser case retries identical audio after 403 then save success. No real central accounts, production records or paid AI calls are used.

Final results are recorded in verification/revision-17-checks.json. Browser responses are synthetic; real authenticated central enrollment changes, provider/device and load acceptance remain pending. This is request-bound enforcement, not an instantaneous push disconnect. A source revocation not yet synchronized cannot be detected; a generation/processing task already in flight may finish. The next gated participation request sees the current committed snapshot. Active-attempt release and accommodation policy still need authenticated verification.

## Delivery estimate

[REMAINING-DELIVERY.md](./REMAINING-DELIVERY.md) records 12–18 tested build sessions from the post-Build-16 checkpoint to a reliable constrained pilot, and 24–38 total for the broader requested scope. This batch counts toward the first range. AWS relocation and unidentified historical repair are excluded. Session counts are planning ranges, not hours or delivery guarantees.


## Final local results

952 backend tests passed (599 existing warning instances); 103 student unit tests and five browser cases passed. TypeScript, release lint and the webpack student production build passed. Release lint retains three existing unused-icon warnings in PracticePreflight. No dashboard or central-service code changed in this batch.
