# Build 36 — human interviews through external Google Meet

20 October 2026. Candidate implementation across backend, dashboard and student frontend. No production deployment, hosted staging, Google API provisioning or live invitations.

## Delivered flow

An administrator enables the new **human_interviews** feature per person: read, create and update are independent operations, with read required for writes. This feature is explicitly opt-in for non-administrators; legacy NULL permissions do not grant scheduling. Existing active course/college membership pairs remain required. The course roster links to Human interviews when enabled. Staff need course-workspace read access to navigate there normally; the human API independently enforces its own permissions and scopes.

The scheduler hosts their own appointment. They search currently eligible students in a course, enter a title, a manually created standard Google Meet URL, a start in device-local time and 5–180 minutes duration. The API requires explicit timezone offsets, converts to UTC, accepts starts within 180 days, and rejects unsafe links and overlapping scheduled appointments for either participant. Ordered participant locks prevent concurrent double-booking; request fingerprints and unique request keys protect unchanged retries. No email or Calendar invitation is sent. Verify Google admission/account policies with the actual host before pilot use; saving a link does not verify room ownership or validity.

Students open **Human interviews** from Home. Appointment reads and joins require current, fresh Sampark enrollment. Staff lists and actions use current personal permission and paired course/college scopes; non-admin hosts see only their own appointments. Staff can retain authorized appointment metadata and cancel after enrollment is revoked/stale, but cannot join or report a result. Student join also rechecks the host's current local permission/membership scope. Meeting URLs are omitted from list responses. An audited join-access request releases only a validated Meet link, from 15 minutes before the start until the scheduled end. The user then opens it in a separate tab with no opener/referrer. Refresh removes stale UI links; every new release checks the server again.

The host explicitly reports completed, cancelled, no-show or technical issue, with a reason and expected version. No-show reporting is blocked until the scheduled end; completion cannot be reported before the start. Appointment locks/version checks prevent conflicting updates. Closed appointments cannot be joined or reopened. To change host, timing or link, cancel and schedule a replacement. A technical issue/no-show never launches AI automatically and never creates a score. Recording a reported outcome is distinct from verified attendance.

## Boundaries and remaining work

This is a standalone human appointment, separate from practice and formal AI attempt state. It neither changes published question counts nor creates AI attempts/answers/receipts. Published template/rubric attachment, human structured scorecards, evidence review/appeals, additional interviewer assignment and a coordinated AI/human handover remain future increments. AI assessments continue to use their existing AI-generated/curated voice questions; selecting this human appointment does not turn them into human-only assessments.

Google Meet handles admission, audio and camera in the external tab. No embedded video, automatic room creation, recording, attendance detection, calendar synchronization or automatic invitations are implemented. A copied meeting link cannot be revoked by Samvaad; hosts must manage admission or replace the room in Google Meet. Local authorization cannot revoke access already granted by an external provider. Actual Google account/device/admission acceptance remains a pilot gate. No zero-cheating guarantee is made.

## Verification

- New backend cases: 21 focused tests pass, including real PostgreSQL scheduling/retry races, scope/freshness/personal permission/membership revocation, enrollment-revocation cancellation, early/closed join windows and explicit outcome/version rules.
- Full backend final suite: **1,297 passed**, 614 existing warnings. Local runtime Python 3.14.8/PostgreSQL 15.17; production Python 3.12/PostgreSQL 17 requires exact GitHub CI and pilot verification.
- Student: 259 unit tests, 116 browser scenarios, webpack production build and TypeScript pass. Three new browser scenarios cover fresh link release/revocation, technical-issue guidance and rejecting an unexpected URL.
- Dashboard: 37 Node tests, 43 browser scenarios, production build and TypeScript pass. Two new scenarios cover retry-key preservation, external link controls, versioned cancellation and revoked access.
- Targeted changed-source lint passes; student broader release lint reports four existing warnings (unused icons/legacy effect cleanup) with zero errors.
- Phone preview inspected from the real student component using fixture authentication/API responses. Browser tests are synthetic API integration, not a real Google call or physical-device acceptance.

Additive migration head: a5b6c7d8e9f0. Apply with the existing controlled release/backup process before enabling this new capability. Roll back application features first if necessary; do not drop appointment records to roll back an application release. AWS relocation remains deferred. No hosted staging environment will be provisioned.
