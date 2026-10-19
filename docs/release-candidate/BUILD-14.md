# Build 14 — scoped roster reporting and controlled export

Implemented 19 October 2026; release candidate only, not deployed.

## Delivered

Course workspace now provides server-side name/college substring search, completed-practice activity filters, pagination and matching-cohort totals. “No completed practice” includes students with unfinished sessions; it is not a claim that they never started. Counts cover the entire matching authorized cohort, not just the current page. Practice counts include only completed sessions explicitly bound to this course, excluding formal assessments, other courses and untagged legacy work.

Search, aggregate, list and export share the same current course/college/purpose consent/enrollment freshness predicate. Search `%`, `_` and backslash are treated literally. Searches are trimmed and capped at 100 characters. No student email or phone lookup is added. Empty and out-of-range pages retain correct matching totals. The dashboard resets pagination when applying filters, rechecks an unchanged search when requested, and hides earlier rows/counts during loading or an access error.

CSV export is a separate `course_workspace.export` operation in the existing administrator permission catalogue. Non-admin staff require an explicit per-person grant including read; a legacy NULL permission policy does not enable the new export capability. Global administrators retain the normal shared-student enrollment/consent boundary. UI capability flags are server-derived; the handler and router both enforce export access.

POST `/v2/course-workspace/courses/{course_id}/students/export` accepts the same search/activity filters and a trimmed 10–1000 character audit reason. The file includes only student ID, name, college and completed course practice count, across matching pages. Exports above 1,000 students fail with 413 and instructions to narrow filters; no silent truncation or background download URL is introduced. Empty exports contain a header. UTF-8/BOM and CSV quoting support Unicode and embedded commas/newlines. Untrusted spreadsheet formula prefixes are neutralized after control-character normalization.

A successful export commits an audit event before responding. Metadata contains course, row count, activity filter and whether search was applied, without copying the search text or roster. Audit failure prevents file disclosure. CSV and roster responses use `Cache-Control: no-store`; CSV responses also set attachment and nosniff headers. The dashboard exports applied filters even if the user has typed an unsubmitted draft search. Downloaded files are snapshots and cannot be revoked remotely; the interface explains this.

## Validation

- Backend full suite: **922 passed** (599 existing warning instances), including real PostgreSQL migration/schema tests and four new reporting cases. Real-database checks cover paired grants across multiple memberships, wrong course/college, stale enrollment, missing/withdrawn consent, excluded assessment/legacy/in-progress sessions, literal wildcard search, pagination-independent totals, invalid filters, explicit export grants and revoked membership.
- Ten new unit cases cover spreadsheet formula defenses, Unicode/empty cells, export operation separation, trimmed reasons, the 1,000-row boundary and audit failure.
- Dashboard: TypeScript, Biome, **37 Node tests**, **eight Playwright cases** and production build passed. New browser case verifies filter/page reset, unchanged-search refresh, actual CSV download, applied versus draft filter semantics, access denial hiding prior evidence and a 390px layout.
- No schema migration is needed. No production records, AWS resources, enrollment assignments or AI providers were changed. Browser API responses are synthetic; authenticated staging, real-device and load acceptance remain pending. The student frontend and central services are unchanged in this build.

## Remaining

This closes the course practice roster's search/count/export slice, not all legacy reporting parity. Course-bound placement shortlists/decisions and remaining staff actions still require their own purpose-aware implementation; legacy broad reporting guards remain enabled. Production activation still needs approved staff scope setup through the admin workflow and authenticated acceptance. The companion remains scheduled in AI-COMPANION.md; AWS relocation is deferred.
