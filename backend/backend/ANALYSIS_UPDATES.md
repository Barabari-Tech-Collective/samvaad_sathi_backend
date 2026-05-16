# Structure Practice Analysis Notes

This note documents current structure-practice analysis behavior. It is not a test result log.

## Endpoint

`POST /api/v2/structure-practice/{practice_id}/question/{question_index}/analyze`

Implementation: `src/api/routes/interviews_v2.py`

## Current Behavior

- Loads the structure-practice session and validates ownership.
- Reads all submitted section answers for the selected question.
- Detects expected section order from the question framework.
- Combines submitted sections in framework order using section labels.
- Marks missing sections as missing in the response.
- Passes framework, submitted sections, expected sections, answer text, and timing data to `src/services/structure_analysis.py`.
- Stores analysis on the most recent submitted section answer when a target answer exists.

## Scoring Shape

The analysis response includes:

- `frameworkProgress.frameworkName`
- per-section `status`, `answerRecorded`, and `timeSpentSeconds`
- `completionPercentage`
- `sectionsComplete`
- `totalSections`
- `progressMessage`
- `timePerSection`
- `keyInsight`
- LLM metadata

Completion reflects both submitted coverage and answer quality. Partial analysis is allowed, so clients can call analysis before all sections are complete.

## Related Files

- `src/api/routes/interviews_v2.py`
- `src/services/structure_analysis.py`
- `src/services/progressive_hints.py`
- `src/repository/crud/structure_practice.py`
- `src/models/schemas/structure_practice.py`
- `src/models/db/structure_practice.py`
