# Build 24 — interview history and feedback recovery

Implemented 20 October 2026 in backend and student release candidates. Production remains unchanged.

## History continuity and honest session states

Both cursor-based interview repositories now return the last delivered row as the next cursor. Previously they used the lookahead row, causing the next page to skip that interview. PostgreSQL contracts traverse 43 mixed-owner rows at limits 1, 2, 20 and 100 through both repositories.

The basic history endpoint now includes explicit practice/assessment mode, expiry and report-record presence without exposing question plans or private audio. Record presence does not certify report readiness or validity. Score extraction accepts bounded numeric percentages, preserves zero and treats malformed or missing values as unavailable. The enhanced with-summary score parser is unchanged and is not used by the rebuilt History page.

The responsive History page retains paused, preparing, closed and unknown statuses instead of dropping everything except active/completed. Filters distinguish saved sessions, completed work and closed/other records. Counts describe loaded rows, not a claimed total. Older pages load on demand, deduplicate IDs and reject invalid or repeating cursors. Failed pagination retains previously loaded rows and offers retry; access denial hides cached rows. New records require refresh: pages are not a transactional snapshot.

## Safe continuation and account boundaries

History reads no longer start a session as a side effect. Only active or paused sessions with recognized explicit mode, an unexpired deadline and supported continuation rules offer a link. Paused assessments, preparing sessions and unknown modes remain visible with recovery copy. Links carry the original session ID and mode; they do not copy questions into session storage or silently convert an assessment to practice.

Mode/expiry metadata is advisory. Actual continuation still checks current enrollment, the saved plan, ownership and device/lease rules. Viewing owned historical evidence and authorization for a new interview action remain separate policies; this build does not invent enrollment grants.

History, report and receipt queries are scoped by authenticated account. Account changes remount the journey, clear retained pages and prevent late responses from appearing under another identity. Requests support cancellation through the existing authenticated Axios client. Queries do not automatically retry errors.

## Report and independent receipt recovery

Invalid IDs cause no report request. Missing reports, denied access, transport/server failure and malformed core report envelopes have distinct recovery messages and manual retry. A missing report is not presented as proof that a background job is running. Refresh does not keep displaying stale feedback as if it were current. Partial reports do not replace absent evidence with a zero score.

An owned submission receipt can render independently when feedback is unavailable, including the planned, saved, skipped, unresolved and unreached breakdown and the recorded completion reason. This neither regenerates feedback nor reopens or rewrites an assessment. Receipt and report endpoints retain their own authorization. Client validation checks the core report envelope; deeper nested report data still relies on the backend schema.

## Verification and rollout

- Backend: 1,095 tests passed, including 9 new PostgreSQL/HTTP contracts and 17 score-parser unit cases; 614 suite warnings remain.
- Student: 175 unit tests and 37 Chromium browser cases passed, including 15 new history/report cases.
- TypeScript, release lint, webpack production build and diff checks passed. Release lint reports three existing unused-icon warnings.
- Browser cases cover access denial after caching, account changes during pending requests, cursor loops, failed older pages, expired/unsupported continuation, missing/denied/unavailable/malformed reports, partial feedback and an independently retained receipt.
- Local screenshots at 1280px and 320px were reviewed for history and report recovery. These render actual pages with synthetic auth/data, not authenticated production acceptance.

Deploy the backend candidate before the student candidate. Refresh History after rollout so clients restart pagination with corrected cursors. No schema migration, historical data repair, cloud/provider call, role grant or production deployment occurred. Dashboard and central service are unchanged and were not rerun.

Real Sampark/identity acceptance, full authenticated shell, physical devices, screen readers, providers and representative load remain open. The seven historical staff membership course decisions still require approved assignments rather than guessed grants. Next work returns to scoped staff course workflows and coordinated acceptance. AWS relocation is deferred; media, Jev evaluation and the companion retain their separate plans.

Build 24 is the eighth batch against the post-Build-16 estimate in REMAINING-DELIVERY.md; this does not reset that estimate.
