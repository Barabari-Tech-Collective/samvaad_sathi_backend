# Build 16 — course practice support queue

Implemented 19 October 2026; release candidate only, not deployed.

## Delivered

A course-bound practice support page now complements completed-history/coaching and placement review. The course roster shows a link only for an authorized coaching responsibility paired to this course and an explicit `practice_support.read` personal grant (global administrators bypass the personal grant). The route independently enforces current coaching course/college scope, enrollment freshness and facilitator-coaching consent. Placement responsibility alone is insufficient. The existing course-workspace read restriction still applies to the route. No user's grants are changed automatically.

GET `/v2/course-workspace/courses/{course_id}/support` supports bounded pagination (1–100 rows, default 25) and four signal filters. It shows one primary signal per practice session, in this precedence:

1. `session_blocked`: failed/terminated status or existing proctoring termination flag. This is not evidence of misconduct.
2. `report_delayed`: completed session older than 15 minutes with no nonempty report object. Missing rows, SQL/JSON null and empty reports remain delayed. This checks presence, not the semantic quality of a report.
3. `open_over_two_hours`: active/paused/in-progress session created over two hours ago. Creation age is not proven inactivity; the UI does not call the session stuck.
4. `answer_processing_failed`: one or more answer records currently have failed transcription status.

The query binds every session to the course and currently shared student predicate before aggregation or pagination. Formal assessments, other courses, untagged legacy work, staff accounts, unshared students and stale enrollment are excluded. Matching total and failed-answer count cover the same filtered cohort across pages. A higher-priority primary signal does not erase the failed-answer count shown on the session.

Only session ID, student ID, status, creation/completion times, primary signal and failed-answer count are serialized. No prompts, scores, transcript, audio URLs, provider error details or direct database-edit controls are returned. The history link opens the existing shared completed-practice view; it is not a session repair console. No reset, regeneration, retry, cheating decision or message to a student is performed by this queue.

Successful reads commit an audit record before returning the queue; audit failure prevents disclosure. Responses are no-store. The dashboard hides old rows/counts during refresh or errors, resets pagination on filter change, supports manual access recheck and fits a 390px viewport. An empty result means no current matching shared signals, not proof that all platform sessions are healthy.

## Corner cases verified

- Explicit support permission versus legacy NULL policy; placement-only responsibility rejected; removed feature access fails closed.
- Multiple course/college grant pairing; stale enrollment; missing/withdrawn coaching consent; excluded legacy/formal/other-course work.
- Missing versus present reports; null and empty report placeholders; recent completion below threshold.
- Failed answers counted in matching sessions while hidden/formal session failures are excluded; primary-signal precedence retained.
- Pagination beyond available results, filtered counts, invalid signal rejection, content whitelist and audit-write failure.
- Browser filtering/page reset, cautious status wording, correct history route, narrow-screen overflow and revoked access hiding old evidence.

## Verification

Backend full suite: **944 passed**, including three new real-PostgreSQL support cases and four unit cases. Dashboard: TypeScript, Biome, **37 Node tests**, **ten Playwright cases** and production build passed. Existing warning categories remain. No new schema migration is required (head stays `f4a5b6c7d8e9`). All database tests used the isolated local database; browser backend responses are synthetic. No production records, scopes, provider calls or AWS resources were changed. Student frontend and central services are unchanged.

## Remaining acceptance

This closes a scoped read-only support slice; it does not implement repair actions or prove every edge case in the full platform. Authenticated central identity/enrollment staging, production-like volumes, supported-device acceptance and operational investigation procedures remain open. Continue explicitly distinguishing automated checks from those gates. Full visual acceptance, human/video integration, calibrated integrity/Jev work, active-attempt enrollment-revocation behavior and remaining role workflow coverage remain tracked separately. Companion design remains scheduled in AI-COMPANION.md; AWS relocation stays deferred.
