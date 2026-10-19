# Build 20 — practice receipt integrity and enrollment ordering

Implemented 20 October 2026; backend release candidate only. Production remains unchanged.

## Problem and behavior

An independent practice receipt key previously returned success for any later payload with the same student/key, including a different module, level, evidence kind or evidence size. It could also return a receipt belonging to an assigned drill without its assignment context. Assignment retries checked only some receipt fields and could crash on a missing legacy receipt.

A shared exact-payload check now compares student identity, module, level, receipt key, evidence kind and evidence units. Independent retries additionally reject a receipt linked to an assignment. Both the ordinary read path and unique-constraint race recovery perform these checks. Reusing a key with changed details returns 409; an exact retry returns the original receipt without adding progress. Receipt keys remain scoped to the student, so two students can use the same client key independently.

Completed assignment retries validate their stored receipt with the same check. Missing, foreign-owner or mismatched legacy evidence returns a controlled conflict rather than a crash or invented completion. Existing independent receipts cannot be attached retroactively to an assignment, and an assigned receipt cannot be reused for another assignment or independent practice. Existing completed evidence is retained; no historical rows are repaired or reassigned automatically.

## Enrollment and assignment concurrency

Assignment completion, creation and cancellation acquire the student lock before the assignment lock. A committed enrollment change is therefore observed before a waiting assignment mutation validates and commits. If completion acquires the lock first and commits, a subsequent revocation preserves that accepted receipt. Exact retries of already-completed assignments remain available after enrollment revocation; new assigned completions still require current enrollment and, when sync is enabled, a fresh Sampark snapshot.

Staff creation/cancellation re-run course, paired responsibility, consent and feature authorization after waiting for the student lock. Locked student/assignment reads refresh cached ORM rows. This recheck does not claim transactional serialization of every separate staff-membership or consent writer; those retain their own authorization contracts and still need authenticated acceptance.

The PostgreSQL student lock uses NO KEY UPDATE. It serializes ordinary enrollment UPDATEs without blocking foreign-key key-share checks for independent practice inserts. All these assignment writers follow user-before-assignment order; the receipt collision regression checks independent insertion while an assignment holds its user lock.

## Verification

Final results are recorded in the local blueprint verification/revision-20-checks.json. Tests run against the isolated local PostgreSQL database. New cases include exact versus altered receipts, simultaneous matching/conflicting writes, assignment context, legacy inconsistencies, both enrollment-update orderings, restored enrollment, stale/future/missing/wrong-source snapshots, real staff course authorization after waiting, receipt ownership and the foreign-key lock collision. Existing cancellation/completion, due-date, audit, ownership and schema suites are rerun. Final full backend suite: **1,035 passed** (599 warnings), including **27 new PostgreSQL regression cases**. `git diff --check` passed. Frontend code is unchanged, so frontend checks were not rerun in this batch.

No schema migration, frontend or central-service change. No cloud-resource, production enrollment, role grant or scoring change. Receipt evidence remains client-reported activity; validating payload identity does not prove speech quality or skill attainment.

## Release limits and next work

This validates local transaction behavior. Real Sampark sync/revocation, authenticated roles, device journeys and representative load remain staging/pilot gates. Enrollment freshness enforcement follows the existing opt-in sync configuration; this batch does not enable it in production. API writers must roll out together after old writers drain; old code does not enforce these new checks. Transactions which lose access return the existing 403/404 contracts without committing an assignment or audit change.

Build 20 is the fourth batch against the post-Build-16 estimate in REMAINING-DELIVERY.md, not a reset of that estimate. The next planned block is critical responsive student/staff journeys and first-use guidance, alongside the remaining authenticated acceptance gates. AWS relocation remains deferred.
