# Build 15 — course placement review and finalization

Implemented 19 October 2026; release candidate only. No production activation.

## Workflow

Course workspace exposes a placement-review link only when the server confirms placement responsibility for that course and the new `placement_shortlists.read` feature permission. A direct review URL performs the same server checks. Non-admin staff require explicit per-person operations; a NULL legacy permission policy does not grant this new feature. Operations are read, create, update (human decisions), publish (finalize), and delete (archive a draft). Administrators can select them using the existing personal-access editor; no existing user's grants are guessed or modified.

Placement course and college grants remain paired per membership. A coaching-only responsibility, or a placement responsibility for a different course, cannot authorize review. Candidates require current `opportunity_readiness` consent and fresh enrollment for this course. Coaching consent alone is not a substitute. Staff accounts and global-admin accounts are excluded from the student pool. Global administrators also use current opportunity consent/enrollment for candidate access.

1. Create a named opportunity/target-role draft with an evidence window, reference minimum completed rounds and a timezone-aware expiry in the next 365 days. Creation UUIDs bind to a normalized payload; identical retries return the same draft and changed-payload retries conflict.
2. Search and page currently shared candidates. Practice evidence is bounded to completed, dated sessions in this course and window. It excludes formal assessments, other courses, untagged legacy work, incomplete sessions and undated completions. The target role is review context, not an automatic validation that a practice track satisfies that role.
3. Record explicit human include/exclude decisions with reasons. Missing and insufficient evidence remain distinct states, never zero scores or automatic rejection. No AI ranking, score, transcript, recording or feedback text is used. Each decision stores the metadata snapshot at decision time. Reference minimum/window are guidance, not automated eligibility gates.
4. Save against the current shortlist version. Every mutation locks the parent row; stale saves conflict. Refresh is required after an uncertain decision save or version conflict. Candidate pagination/search totals use the same current placement predicate.
5. Finalize reviewed decisions with a reason and explicit UI acknowledgement. At least one decision must exist, every recorded decision must still be reviewable by the finalizer, and the draft must not be expired. Undecided students remain undecided; they are not silently excluded. If consent, enrollment or college/course access no longer permits a decision, finalization stops rather than silently omitting it.
6. A draft owner with delete/archive permission, or an administrator, can archive a blocked or expired draft and create a replacement. Archiving retains its decision history and does not notify students/employers. Finalized reviews cannot be archived or edited. This build does not send employer communications or export placement decisions.

## Privacy and persistence

Finalized/archived detail pages include only recorded decisions whose students remain currently reviewable; counts do not disclose hidden entries. Consent withdrawal hides identity, reason and evidence snapshot. Stored decisions are retained according to existing governance policy. Current metadata and the snapshot at decision time are labelled separately; the current view can change with new practice, elapsed time or sharing while the saved finalized decisions stay fixed.

Alembic revision `f4a5b6c7d8e9` follows `e3f4a5b6c7d8`. It adds nullable course/request fields and a per-creator request-key unique constraint to the existing shortlist table. Untagged historical rows remain untouched and absent from the new course routes. PostgreSQL triggers protect finalized course parent records and child decisions, including against direct SQL edits/deletes and moving entries between parents. The existing student-account deletion cascade is preserved: deletion of the account can remove its private decision entries without changing other finalized decisions.

New APIs live under `/v2/course-shortlists/courses/{course_id}`: list/create, `/{shortlist_id}` detail, `/decision` PUT, `/finalize` POST and `/archive` POST. All handlers reauthorize each operation. List/detail responses use no-store; detail views and mutations commit audit events before returning evidence or success. Create the migrated backend before deploying the dashboard. A rollback must account for this new schema and finalization guards; do not discard course attribution through an unreviewed downgrade.

## Verification

- Backend full suite: **937 passed**, including real PostgreSQL permission/consent, course/college pairing, idempotent creation, version conflicts, draft expiry, archive recovery, decision/finalize race, migration/schema and finalized-record trigger checks. The final guard check includes preserving student deletion cascades. Existing warning categories remain.
- Dashboard: TypeScript, Biome, **37 Node tests**, **nine Playwright cases** and production build passed. The new browser case covers an uncertain-create retry with the same payload/key, an explicit human decision with missing evidence, finalization acknowledgement, read-only finalized UI, access revocation hiding candidates, and 390px overflow.
- Browser APIs are synthetic and database tests use the disposable local database. No real student records, production roles, AWS resources, media settings or AI providers were changed. Authenticated staging, supported devices, load acceptance and approved production scope activation remain pending. Student frontend and central services are unchanged.

## Remaining work

This is a course-bound practice-readiness review workflow, not a certified hiring decision system or a replacement for formal assessment. Remaining staff reporting/operations parity and authenticated end-to-end acceptance still need completion. Full responsive visual acceptance, provider/media orchestration and calibrated integrity/Jev work remain open. The companion is planned in AI-COMPANION.md. AWS relocation and unknown historical mapping repair remain deferred.
