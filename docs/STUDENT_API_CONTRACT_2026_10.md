# Student API contract — October 2026 additions

For the student app. These endpoints are on backend branch `feat/staff-tasks`. **They are not on
staging until that branch is pushed and deployed** (with `alembic upgrade head`). All paths are
under the backend's API root; the student app calls them like its other `v2/...` endpoints, signed
in as the student. Every response is `Cache-Control: no-store`.

Nothing in the existing student contract changed shape.

**Student app note:** `src/lib/session-axios.ts` only lets through an allowlist of path prefixes.
Add `v2/learning-plans/` and `v2/mock-assignments/` to it, or these calls will be refused on the
client. The two behaviour changes are marked
**Behaviour change**.

---

## 1. Learning plans

A plan is a goal with ordered steps. Steps are done in order. A learner has a plan because staff
assigned it (`source: "assigned"`) or because they started an open plan (`source: "self"`).

Step kinds and how each completes:

| `kind` | What the learner does | Completes when |
|---|---|---|
| `drill` | The drill named by `module` (`warm_up`, `think_fast`, `pronunciation`, `structure`, `pace`) | The learner records that drill (the existing drill completion call) after the previous step was done. Automatic. |
| `mock` | A mock interview of template `job_profile_id` (`job_name` for display) | The learner finishes a mock of that template in the plan's course after the previous step. Automatic. |
| `reflection` | Writes a short reflection | The app posts it (below). |

Progress is worked out by the server on every read; the app never reports a drill or mock step as
done.

### `GET v2/learning-plans/me/plans`
```json
{ "items": [ {
  "id": 12, "course_id": "course-a", "source": "assigned",
  "status": "active",            // active | completed | cancelled
  "version": 1, "reason": "Prepare for the project viva.", "due_date": null,
  "created_at": "...", "closed_at": null,
  "plan": { "id": 4, "title": "Explain your project", "goal": "...", "version": 1 },
  "completed_steps": 1, "total_steps": 3,
  "steps": [ {
    "id": 30, "position": 1, "kind": "drill", "title": "Warm up your voice",
    "instructions": null, "module": "warm_up", "module_label": "Warm-up chat",
    "job_profile_id": null, "job_name": null,
    "status": "done",             // done | current | upcoming
    "completed_at": "...", "reflection": null
  } ]
} ] }
```
Exactly one step is `current` while the plan is `active`. Send the learner to it:
- drill → the existing drill page for `module`
- mock → start a mock with `job_profile_id`
- reflection → a text box, then the call below

### `GET v2/learning-plans/me/available`
Published plans the learner may start themselves. Same plan shape as `plan` above plus `steps`
(without `status`).

### `POST v2/learning-plans/me/start`
```json
{ "request_key": "<uuid, one per tap>", "plan_id": 4, "course_id": "course-a" }
```
`201` with the assignment (shape as in `/me/plans`). Retrying with the same `request_key`, or
starting a plan already in progress in that course, returns `200` with the existing one.
`422` when the plan is not open to self-start; `403/404` when the learner is not currently enrolled
in `course_id`.

### `POST v2/learning-plans/me/{assignment_id}/steps/{step_id}/reflection`
```json
{ "text": "At least 20 characters." }
```
`200` with the updated assignment. `409` if this is not the learner's current step or the plan is
no longer active.

---

## 2. Mock interviews a teacher asked the learner to take

### `GET v2/mock-assignments/me`
```json
{ "items": [ {
  "id": 7, "course_id": "course-a", "job_profile_id": 31, "job_name": "Analyst mock",
  "reason": "Try a full analyst mock interview", "note": null, "due_date": null,
  "status": "assigned",          // assigned | completed | cancelled
  "version": 1, "interview_id": null, "created_at": "...", "closed_at": null
} ], "total": 1, "page": 1, "limit": 25,
  "notice": "Mock interviews are rehearsal. Taking one never counts toward selection." }
```
Start the mock with `job_profile_id`. It turns `completed` by itself once the learner finishes a
mock of that template in that course. These are **not** in `GET v2/coaching-assignments/me`, which
still returns speaking drills only.

---

## 3. Opening outcomes

### `GET v2/opportunities/me/outcomes`
Released results of openings the learner was a candidate for.
```json
{ "items": [ {
  "opportunity_id": 12, "revision": 1, "title": "Associate Data Analyst",
  "organisation": "Infosys", "decision": "include",   // include | exclude
  "feedback": "...", "next_step": "...", "released_at": "..."
} ] }
```
Only the learner's own released outcome, with the feedback and next step staff approved. Internal
reasons are never sent.

---

## 4. **Behaviour change:** assessment results held for review

A staff member may set an assessment template to "review before release". For sittings of such a
template, until staff release the result:

- `GET v2/summary-report/{interview_id}` and `POST v2/summary-report` return a normal report envelope
  with `"reportStatus": "needs_review"`, `candidateInfo`, `"questionAnalysis": []`, an
  `uncertaintyNotice` saying the result is being reviewed, and **no** `scoreSummary` or feedback.
  The app already shows this as "Feedback needs review".
- `interviews-with-summary` rows show no percentages or action items for that sitting.
- The older `summary-report/{id}`, `final-report/...` return `409` with a plain message.

Once released, the same calls return the full report.

## 5. **Behaviour change:** corrected scores

Staff may correct an AI assessment score. The report then carries the corrected percentages, and,
if staff wrote one, `uncertaintyNotice` reads "Your score was updated after review. …". No new
fields.

---

## 6. Notifications (new)

Saved in-app messages. Nothing private is ever written into one; opening the link checks current access.
Created for: a human interview scheduled, rescheduled, cancelled or its result released; an AI
assessment result released; a mock interview, learning plan or coaching exercise assigned; an opening
decision released.

### `GET v2/notifications/me?unread_only=false&limit=30`
```json
{ "unread": 2, "items": [
  { "id": 9, "kind": "human_interview_rescheduled", "title": "Interview time changed: Technical panel",
    "body": "Your interview has a new time.", "href": "/human-interviews",
    "created_at": "...", "read": false } ] }
```
`href` is a student-app path (`/human-interviews`, `/assessments`, `/mock-interviews`, `/coaching`,
`/opportunity-outcomes`). Use `unread` for a badge; poll about once a minute.

### `POST v2/notifications/me/{id}/read` and `POST v2/notifications/me/read-all`
Marks one, or all, as read. Only the signed-in person's own can be changed.

Add `v2/notifications/` to the student app's allowed path list in `session-axios.ts`.

Emails about interviews are now sent by the worker (within about a minute), from the interview as it is
at that moment. A reminder goes out about 24 hours and 1 hour before the start, and is dropped if the
time changed or the interview was cancelled. The dashboard card is correct whether or not an email
arrived.

## 7. Human interview scorecards (staff only; no student change)
Students still see only the released `result` and `feedback`, exactly as before. Private notes and
the reviewer's note are never returned by any student endpoint.
