# Placement interviews: design (proposal, not built)

Status: proposal for approval. Nothing here is implemented. Written after the student usability
test, because the current "Human interviews" page cannot cover how the placement team works.

## 1. The problem

Companies ask Barabari to interview candidates and send back the best ones with scores. The
placement team may or may not attend each interview. Today the system has two separate things:

- **Human appointments** (`human_interviews.py`): staff schedule a Google Meet for a course. Samvaad
  does not record it, score it, or switch to AI. If it is cancelled the student is told to ask the
  programme team.
- **AI assessments** (assessment templates, formal sittings, integrity review): strict, recorded,
  reviewable.

Placement needs one object that can be run by AI, by a person, or by both, and ends in one
scorecard that can be given to a company.

## 2. Name

Student-facing: **Interview** (always with the company or role name, for example "Interview: Acme,
Support Executive"). Staff-facing: **Placement interview**. Retire the name "Human interviews".
Practice stays "Practice". Formal tests stay "Tests".

## 3. Mode, chosen by the placement team per interview

| Mode | Who asks the questions | Where it happens | Score |
|---|---|---|---|
| **AI only** | AI | Inside Samvaad | AI scorecard (strict profile) |
| **Person only** | The interviewer | Google Meet, outside Samvaad | Interviewer fills in a scorecard on the dashboard |
| **AI + person** | AI first, then the interviewer | Part 1 inside Samvaad, part 2 on Google Meet | One combined scorecard |

"Both present at the same moment" is deliberately not version 1 (see section 6).

## 4. The interview object

One record, `placement_interview`, with these parts:

- `candidate` (student), `course`, `company`, `role_title`, `created_by` (placement staff).
- `mode`: `ai`, `person` or `ai_person`.
- `parts`: an ordered list. Each part has `kind` (`ai` or `person`), `status`, `window_start`,
  `window_end`, and for `ai` a link to the assessment template and sitting, for `person` a Meet
  link and the interviewer.
- `status`: `scheduled`, `in_progress`, `awaiting_review`, `scored`, `shared`, `cancelled`.
- `scorecard`: AI score (strict rubric), interviewer score, written notes, `final_recommendation`
  (set by a named staff member), `visible_to_student`, `shared_with_company_at`.

Reuse, do not rebuild: the assessment engine and integrity review for the AI part, the existing
appointment code for the Meet part and access checks, and the existing audit tables.

## 5. Strict mode

Practice stays lenient. Placement uses a separate scoring profile ("recruiter strict"): fixed rubric
per role, no retries inside a sitting, answers recorded, integrity events kept as **unverified
observations** (never as proof). A rubric version is stored on every scorecard so scores stay
explainable later. Changing a rubric never rewrites an old score.

## 6. AI and a person in the same interview

Three options were considered.

1. **Two parts in order (recommended for version 1).** AI part, then Meet part. One scorecard.
   Simple, reliable, and fair to every candidate.
2. **AI interviews, a person reviews.** The AI runs the whole interview; the interviewer watches
   the recording afterwards and adds a score. Good when staff cannot attend. Can be added as a
   `review` part later with almost no new screens.
3. **Person leads, AI listens and scores live.** Hardest, most failure-prone, and the AI would be
   scoring a call that Samvaad does not host. Not recommended until options 1 and 2 are proven.

## 7. Student flow

1. The student sees "Interview: <company>, <role>" with the date, the parts, and what to prepare.
2. AI part: starts only inside its window, device check first (same as practice), plain-language
   instructions, one attempt, answers saved as they go, a clear "Saved" at the end.
3. Person part: shows the time and a single "Join" button that opens Google Meet; the page says
   the interviewer lets them in.
4. If a part is cancelled the student sees "Your teacher will tell you the new time", never an
   automatic switch. A staff member may convert a cancelled person part to an AI part, and that
   action is recorded with the reason.
5. Results: the student sees a short, kind summary once staff approve it; see decision 1.

## 8. Placement team flow

1. Create the interview: pick candidate(s), company, role, mode, parts and windows.
2. See one table: who is scheduled, in progress, awaiting review, scored.
3. Open a candidate: AI recording and score, interviewer score and notes, integrity observations.
4. Set the final recommendation and approve what the student may see.
5. Export a company pack (scorecard only, no raw audio) with an audit entry.

## 9. Decisions, with the recommended answer

1. **Does the student see the AI score?** Recommended: a short summary after staff approval, never
   the raw number before approval. Staff and the company see the scorecard.
2. **If AI and person disagree, who decides?** Recommended: the named placement staff member sets
   `final_recommendation`; both scores stay visible and nothing is averaged silently.
3. **Does the AI result appear instantly?** Recommended: no. It goes to `awaiting_review` first.
4. **What do companies see?** Recommended: scorecard and written notes only. Raw recordings stay
   internal and are shared only with a recorded reason.
5. **Consent.** Students must agree before a placement interview that the placement team and the
   company will see the result. This reuses the existing consent record.

## 10. Build order

1. Rename the student page and menu from "Human interviews" to "Interviews" (done in the copy as
   "Live interviews" for now).
2. `placement_interview` and parts, creation screen for placement staff, student list page.
3. Person part: scorecard form on the dashboard.
4. AI part: strict profile on the existing assessment engine.
5. Combined scorecard, approval, company pack, audit.
6. Optional: person reviews an AI recording (option 2).

## 11. Risks

- Staff creating interviews for the wrong candidate: confirm screen and an undo window.
- AI errors affecting a real job chance: that is why nothing is shown or shared before staff
  approval, and why rubric versions are stored.
- Students on weak phones and networks: the AI part must resume safely after a drop, reuse the
  practice recovery code, and have a plain-language help path for the microphone.
