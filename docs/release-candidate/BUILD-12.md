# Build 12 — course-scoped practice review

Implemented on 19 October 2026; not deployed.

## Delivered

Staff can open feedback for a completed practice session from the course student history. The new GET `/v2/course-workspace/courses/{course_id}/students/{student_id}/practice/{interview_id}/review` reuses current membership/course/college pairing, enrollment freshness and purpose-specific consent checks. Missing or inaccessible students and sessions return the same 404. Formal assessments, another student's work, another course and untagged sessions are excluded by the database query.

The response includes only session identifiers, a finite reported score in the range 0–100 when valid, a summary capped at 500 characters, and availability state. It does not serialize the source report, transcript or audio. Missing reports return an unavailable state. The score retains the source report's scale and is labelled “Reported score”, without a percentage or invented conversion.

Each successful open commits an audit event before returning evidence. An audit failure prevents disclosure. Responses use `Cache-Control: no-store`; the dashboard fetches on opening, removes the query cache when closed, hides data during revalidation or errors, and offers an access-check retry. Feedback is rendered as plain text. The review panel supports narrow screens and keyboard button controls.

## Validation

- Backend unit suite: 782 passed (443 existing warning instances). Includes 15 new review tests covering invalid numeric values, bounded fields, missing student/session, query scope, audit recording/failure, and revoked scope.
- Dashboard: TypeScript check and production build passed; 37 Node tests passed.
- Playwright: all 6 cases passed. New synthetic API browser case verifies feedback loads only on opening, zero scores display correctly, 390px layout has no horizontal overflow, and a subsequent access denial hides earlier feedback.
- No live student data, production mutations, schema migrations or AI provider calls were used. These checks do not substitute for authenticated staging acceptance with central enrollment and real staff identities.

## Next build

Course-bound coaching assignments remain outstanding. Add explicit course ownership to new assignments, permission checks paired to coaching responsibility, creation/retry semantics, cancellation/completion transitions, and a student-owned assignment view. Existing unscoped assignments must not be silently attributed to a course. Then finish remaining scoped reporting actions and authenticated activation acceptance. Existing production rollout gates remain in force.
