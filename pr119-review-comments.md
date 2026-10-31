# PR 119 inline review comments (async-lms)

Line numbers are for the new version of each file (the PR head, commit 23b44ff).
Each block is one comment. Paste it on the given file and line.

---

## backend/meet-worker.js : line 303-304

This update only works when the class is `sync_pending` or `failed`. But a few lines above we already set it to `processing`.

If Google has no conference record yet, we throw a retryable error. BullMQ then retries the job, but now the class is `processing`, so this update changes nothing. We throw a normal error, the job is marked `failed`, and the class becomes `failed`.

So the retry we planned for never really works. Could we think about making this step safe to run twice? For example, allow `processing` here too, or only move to `processing` after we get the Google data.

---

## backend/meet-worker.js : line 190

If `addCohost` fails for good (for example, the facilitator's email is not a Google account), the job fails but the class stays in `creating`. The space already exists at this point.

The facilitator then cannot end the class, because `end` only allows `scheduled`, `active` and `ending`. Can we move the class to `failed` (or `cancelled`) when this step fails for good? Or make the co-host step not block the class from becoming `scheduled`?

---

## backend/services/class.service.js : line 186

The class and the job row are saved first, and we add the job to the queue after the commit. If Redis is down, or the server stops right here, the class stays in `creating` forever. Nothing picks up `pending` rows from `meet_jobs`.

Please think about a small repeat task that finds `pending` jobs whose `available_at` has passed and puts them in the queue again. That is a simple fix for this.

---

## backend/services/class.service.js : line 135

When the same `idempotencyKey` comes again, we return "existing" and do not queue the job again. So if the first request failed after the commit (see the comment on line 186), a retry from the client cannot fix it.

Could we check the class status here, and if it is still `creating`, queue the create job again?

---

## backend/services/class.service.js : line 196

`createMeetJobInTransaction` is a copy of `createMeetJob` in `meetJob.service.js` (about 30 lines of the same SQL). If one changes and the other does not, they will behave differently.

Can we let `createMeetJob` take an optional `client` and use that in both places?

---

## backend/services/meetQueue.service.js : line 28

The BullMQ `jobId` is always `meet-<meetJob.id>`. In `createMeetJob`, a `succeeded` or `failed` row is reset to `pending` and keeps the same id.

BullMQ ignores `add()` when a job with the same id is still kept (we keep completed jobs and failed jobs). So `POST /:id/sync` can return 202 and no job runs.

Can we add `attempt_count` or a random value to the `jobId`, or remove the old job before adding the new one?

---

## backend/controllers/class.controller.js : line 129

Only facilitators (must own the class) and students (must be on the roster) are checked here. Any other logged-in role can read any class.

Please think about an allow list instead: admin, the owner facilitator, or an enrolled student. Everyone else gets 403.

---

## backend/controllers/class.controller.js : line 383

This is a GET request, but it changes data (it uses up the ticket). Chat apps, email scanners, and browser prefetch often open links before the user does. They can use up the one-time ticket, and the student gets a "410 expired" error.

Can we show a small page with a "Join" button that sends a POST, and use up the ticket only then?

---

## backend/controllers/class.controller.js : line 377

This route needs the `Authorization: Bearer` header (`verifyToken`), but it answers with a redirect. A normal browser link cannot send that header, so this only works if the frontend calls it with `fetch`, and then the redirect is not followed as a page change.

Can we return `{ url }` as JSON and let the frontend open it?

---

## backend/controllers/class.controller.js : line 20

`isAdmin` and `isFacilitator` are written again here, and the repo already has `middlewares/isAdmin.js`, `isFacilitator.js` and `isAdminOrFacilitator.js`. Can we reuse those in the routes so the rules are in one place?

---

## backend/services/class.service.js : line 346

`getJoinState` checks the start time, but not `planned_end_at`. A class stays "joinable" until someone presses End. If the facilitator forgets, students can still join days later.

Should we add a check for the planned end time (maybe with a small extra time)?

---

## backend/controllers/googleIdentity.controller.js : line 80

We make a `nonce` and put it in the state token, but we never check it in the callback. The callback is also not tied to the browser that started the flow.

This means someone could start the link flow for their own student account and ask another person to finish it. The other person's Google account would then be linked to the attacker's account.

Can we also set the nonce in a short-lived httpOnly cookie, and check that it matches in the callback?

---

## backend/controllers/googleIdentity.controller.js : line 115

If the state token is expired or invalid, `jwt.verify` throws and we end up in the `catch`, which redirects with `failed`. The frontend has a `state_mismatch` message for expired links, but we never send it.

Can we catch `TokenExpiredError` and `JsonWebTokenError` here and redirect with `state_mismatch`? Then the user sees the right message.

---

## backend/config/googleIdentity.js : line 27

The state secret falls back to `JWT_SECRET`. If someone forgets to set `GOOGLE_IDENTITY_STATE_SECRET`, the same key signs both login tokens and link tokens.

Can we make `GOOGLE_IDENTITY_STATE_SECRET` required (add it to `requiredEnvironmentVariables`) and remove the fallback?

---

## backend/config/pg.js : line 753

This migration runs when `pg.js` is imported. The API, `meet-worker.js` and `worker.js` all import it, so two processes can start and run it at the same time. `CREATE TABLE IF NOT EXISTS` can fail when two sessions run it together, and the whole Meet schema then rolls back.

Can we wrap the migrations in `pg_advisory_lock`? Longer term, a separate migration step (not at app start) would be safer. This file is now over 1000 lines.

---

## backend/config/pg.js : line 793

`grace_period_minutes` and `policy_version` are not used anywhere in the code. The same goes for `attendance_overrides` (line 949), `is_finalised`, `review_required`, and the statuses `ready_for_review`, `finalised`, `cleanup_pending` and `end_unknown`.

Unused columns and states make it harder to understand the flow. Can we keep only what this PR uses, and add the rest in the PR that builds the override and review feature?

---

## backend/config/pg.js : line 902

`raw_participant_data` keeps the full Google payload. It can include display names and phone user data of people who are not students. Do we need all of it? Please think about saving only the fields we use, or deleting it after some time. Same for `ip_address` and `user_agent` in `class_access_events` (line 879).

---

## render.yaml : line 66

The web service gets `GOOGLE_MEET_CLIENT_ID`, `GOOGLE_MEET_CLIENT_SECRET` and `GOOGLE_MEET_REFRESH_TOKEN`, but only the worker calls the Meet API (the web service only adds jobs to the queue). The CI `.env` steps do not need them for the API either.

Can we remove them from the web service so a bug or leak in the API cannot expose the Meet refresh token?

---

## General comment for the PR (not on a line)

1. There are no tests for about 3,000 lines. The best first test is `calculateAttendance` (merging overlaps, cutting to the class window, the 70% rule) and the sync retry path.
2. The Meet space uses one shared refresh token for all classes. If the Google app is in "Testing" mode, this token can stop working after 7 days. Can we add a note on who owns it, how it is renewed, and an alert on `invalid_grant`?
3. The worker is defined in both `ecosystem.config.js` and `render.yaml`. If both run against the same Redis, two workers read the same queue. Please say which setup is the real one.
4. The migration comment says "legacy attendance tables", but I could not find any older attendance tables or code. Can we remove or fix that comment?
5. Small things: `meet-worker.js` imports `meetJob.service` and `meetQueue.service` twice; `StudentSettings.tsx` has typos in the new text ("`?(<p`", "classes.We").
