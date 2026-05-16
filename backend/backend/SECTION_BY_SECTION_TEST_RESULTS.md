# Structure Practice QA Checklist

Use this checklist when validating the section-by-section structure-practice flow. It replaces older static test-result notes that could become stale.

## Required Checks

1. Create a structure-practice session.
2. Confirm the response includes `practiceId`, `interviewId`, `questions`, `framework`, `sections`, `current_section`, and `current_hint`.
3. Submit a valid audio file for the first section.
4. Confirm Whisper transcription succeeds or returns a structured error.
5. Confirm the response increments `sectionsComplete`.
6. Confirm `nextSection` and `nextSectionHint` point to the next expected section.
7. Submit remaining sections in order.
8. Confirm the final section returns `isComplete=true` and `nextSection=null`.
9. Call the analyze endpoint.
10. Confirm the response includes all expected sections, actual recorded time, completion percentage, and a key insight.

## Useful Scripts

```bash
cd backend/backend
python3 scripts/smoke_structure_practice.py
python3 scripts/test_structure_practice_optional.py
python3 scripts/run_all_smoke.py
```

## Endpoints

- `POST /api/v2/structure-practice/session`
- `POST /api/v2/structure-practice/{practice_id}/question/{question_index}/section/{section_name}/submit`
- `POST /api/v2/structure-practice/{practice_id}/question/{question_index}/analyze`

## Expected Frameworks

- C-T-E-T-D: technical answers
- STAR: behavioral answers
- GCDIO: design or architecture answers
