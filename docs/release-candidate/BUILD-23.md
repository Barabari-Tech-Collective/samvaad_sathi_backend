# Build 23 — current-question progress and saved-session readiness

Implemented 20 October 2026; backend and student release candidates only. Production remains unchanged.

## Completed contracts

HUB-COUNT-01 and HUB-ACCESS-01 from Build 22 are implemented in the candidate. Real authenticated source/device acceptance remains open; these are read contracts, not a new permission grant.

The saved-answer count now uses the latest attempt ID for each question in the selected sitting's persisted question set. A single aggregate statement reads that set and its current attempts. Repeated attempts cannot multiply progress. Unbound legacy attempts, attempts from another interview and questions belonging to another snapshot do not enter the count. The denominator is the persisted question set for legacy sessions, including adaptive questions already added to that set.

Durable evidence follows the existing navigation contract: a saved timestamp, nonempty stored audio URL or non-null legacy transcription. Pending/failed speech feedback still counts when audio is saved. SQL NULL and JSON null transcripts do not count. This is a saved-answer count, not a speech-quality or skill score; retained audio still counts if a later navigation choice marks its question skipped. Saved evidence is never deleted to make the count agree.

Fixed plans retain their declared N while question preparation is partial. Empty new snapshots or explicit preparing status return preparing. Invalid/unsupported/overfilled plans return plan_unavailable with both counts null, not guessed N or zero. A snapshot-less legacy sitting with retained evidence is also plan_unavailable rather than claiming a preparation job is running. No historical repair or question regeneration occurs.

## Readiness and selection

The latest owned, unexpired practice sitting in active/paused/preparing status is selected deterministically by created time then ID. Assessments, completed sessions, expired sessions and other students' sessions are excluded. A blocked latest sitting is retained on the card instead of silently choosing another one. History remains available for explicit session selection.

Readiness is ready, preparing, enrollment_revoked, enrollment_unverified or plan_unavailable. Course states reuse the existing participation guard and its enrollment/freshness policy, including opt-in Sampark freshness enforcement. Untagged legacy/self-practice keeps its existing course contract. Only recognized enrollment denials become a readiness state; unexpected verification failures remain request failures, not ready results. Cached ORM rows are refreshed.

Enrollment checks take precedence in the displayed reason, but restoring enrollment does not hide an independent plan failure. Counts can remain unknown while enrollment is blocked. The sitting and accepted audio are retained unchanged.

This is advisory readiness at read time. Actual resume/answer APIs still enforce current enrollment, deadline, ownership, lease/device takeover and other sitting rules. A hub response is not authorization and does not certify media/provider readiness. Enrollment may change immediately after the response; the next protected action rechecks it.

## Student behavior and compatibility

Only an explicit ready state offers Continue saved interview. Preparing or blocked cards keep the session visible with View saved session (History) and a manual Check again action. They do not advertise a replacement mock. Restored readiness re-enables the original session ID. Counts with null values are never shown as zero. The runtime validator accepts them only for recognized blocked states; ready/preparing states still require valid bounded counts.

Older responses without readiness remain readable but are treated as unconfirmed continuation: History is offered. Unsupported readiness or contradictory progress fails through the existing unavailable-plan recovery UI. Deploy backend before the student client. Older student versions can fall back to unavailable progress on new nullable counts; API enforcement still prevents unauthorized resume. Rollback must retain those server guards.

## Verification

The final full backend suite passed **1,069 tests** (599 warnings), including **34 new PostgreSQL/HTTP cases**. Diff checks passed. Student checks: 149 unit tests, 22 Chromium browser cases, TypeScript, release lint and webpack build passed. Release lint retains three existing unused-icon warnings. New browser cases verify the four non-ready cards at 320px and restoration to the original saved session. Existing stale-refresh, account-change, expiry, voice recovery, coaching and guide regressions passed.

New PostgreSQL cases cover latest attempts, detached/foreign evidence, SQL/JSON null, pending/failed audio, fixed/legacy plans, unsupported/overfilled plans, fresh/stale/future/missing/wrong-source enrollment snapshots, restoration, owner/mode/status/expiry filtering, ties, explicit preparation, empty history and compound failures. HTTP cases verify camel-case fields, nullable counts and omission of private audio URLs and question plans. Authentication and source data in these cases are synthetic; no real Sampark pull or provider call occurs.

No migration or database rewrite. No dashboard, central-service, cloud, provider, role-grant or production changes. Visual checks use local 320px fixture screenshots and do not certify full authenticated shell/device/screen-reader acceptance.

## Next and remaining gates

The two Build 22 hub read-contract gaps are closed in code; authenticated source acceptance, actual screen readers/devices and representative load remain open. Continue the responsive history/report recovery journey and core staff course tasks, then run coordinated staging/rollout checks. Zero-cheating guarantees, media integration, Jev evaluation and the companion remain bounded by their existing separate plans. AWS relocation remains deferred.

Build 23 is the seventh batch against the post-Build-16 estimate in REMAINING-DELIVERY.md; it does not reset that estimate.
