# Build 13 — course coaching follow-ups and companion roadmap

Implemented 19 October 2026, release candidate only; no production changes.

## Companion placement

[AI-COMPANION.md](./AI-COMPANION.md) is added to the implementation plan, blueprint and runbook. It specifies a supportive named practice companion and a neutral assessment interviewer, event-driven SVG expressions, reduced-motion/hide/mute preferences and clear human handover. Build during responsive design work after coaching/audio/recovery foundations. Practice acceptance must precede assessment rollout. No character has been implemented in this batch.

## Coaching workflow delivered

- Staff with a coaching responsibility paired to this course/college, current student enrollment and coaching sharing consent can list, create and cancel follow-ups from the course student page. Placement responsibility alone cannot authorize this workflow. Personal `course_workspace.read` and `practice_coaching.read/create/update` permissions additionally restrict each operation; global administrators still require the shared-student enrollment/consent predicate.
- Assign Warm-up chat or Think fast with a reason, optional student-visible note and optional UTC due date. Only these two modules are connected to assignment completion in this build. Minimum reason length is checked after trimming. Past dates are rejected on creation; late completion remains allowed.
- Creation carries a UUID request key and normalized payload fingerprint. The same request returns the original assignment; changed payloads conflict. The dashboard retains the request while an uncertain save is retried, and lets users correct definite validation errors. It does not persist drafts across reloads; check the list before restarting an uncertain request after navigating away.
- Cancellation requires the current version, a reason, and an open assignment. Assignment row locking serializes cancellation and completion. Completed/cancelled rows are retained; replacement work is a new assignment.
- Students see their own paginated follow-ups in Practice, with status, course ID, reason, note and due date. Start links carry the assignment ID. Before recording, ownership, module, open state and enrollment availability are checked; completion checks enrollment again.
- The existing voice drill reports the assignment ID with its completion receipt. Receipt insertion, assignment transition and audit commit atomically. Another student's assignment, mismatched module, non-voice completion, revoked enrollment, cancelled task and reused general-practice receipt are rejected. Concurrent retries return one receipt. The UI requires the server to echo the linked assignment ID before showing “Assignment complete”. Failed assigned saves stay retryable in memory and are not queued in the general-practice offline queue.
- Completion is explicitly **student-reported practice activity, not a verified skill score**. It does not upload/store the drill recording or claim independent verification. Saved assignments remain visible to their owner after sharing withdrawal; staff access follows current consent. Losing enrollment blocks new completion, without erasing history. A staff departure does not silently cancel already assigned work.

## Data and rollout

Additive Alembic revision `e3f4a5b6c7d8`, following `d2f3a4b5c6d7`, creates `coaching_assignment`. Existing `practice_assignment` rows are untouched and never assigned a guessed course. New endpoints are `/v2/coaching-assignments/me`, `/me/{assignment_id}` and `/courses/{course_id}/students/{student_id}` (GET/POST), plus `/{assignment_id}/cancel` (POST). The existing `/practice/drills/complete` accepts optional `assignmentId` and echoes it on success.

Deploy the migrated backend before either client. Preserve the coordinated release gate if rolling back: do not leave assignment clients connected to a backend that ignores assignment IDs. No production database migration, rollout, account move, provider call or real student-data mutation was performed here.

## Verification

- Backend full suite: 908 tests passed, including disposable PostgreSQL schema/migration checks and ten new assignment lifecycle cases. Scenarios cover ownership, enrollment revocation, old receipt reuse, exact retries, mismatched modules, cancellation/version conflicts, concurrent completions, cancel/complete race, preflight and late completion. Final response serialization changes also receive the unit-suite check.
- Dashboard: TypeScript, Biome, 37 Node tests, seven Playwright cases and production build passed. New browser case covers a correctable 422, uncertain save retry with identical UUID/body, and cancellation; 390px overflow checked.
- Student: TypeScript, 101 Vitest tests, four Playwright cases and webpack production build passed. New browser case uses real MediaRecorder with a synthetic mic, preserves the same linked receipt across a failed save and confirms assignment completion. Expanded release lint passes with three existing unused-icon warnings in PracticePreflight.
- Local default Turbopack builds fail because their worker port is blocked by this environment; the supported `next build --webpack` production build succeeds. This is not a verified Turbopack build. Browser APIs use synthetic backend responses; authenticated staging and real-device acceptance remain pending.

## Next work

Complete the remaining scoped reporting/search/export/action parity and authenticated staff/student acceptance. Extend assignment adapters to pronunciation, pacing and answer structure only when those modules can emit appropriate completion records. Companion design remains scheduled in the responsive design phase. Existing media/provider, integrity calibration, historical mapping and production activation gates remain open; AWS relocation stays deferred.
