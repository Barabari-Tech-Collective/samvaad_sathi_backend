# Structure Practice API

Structure practice lets a user answer one interview question section by section. The backend detects or assigns a framework, returns the sections in order, accepts one audio recording per section, transcribes it with Whisper, and analyzes the combined answer.

Base path: `/api/v2`

## Frameworks

- C-T-E-T-D: `Context`, `Theory`, `Example`, `Trade-offs`, `Decision`
- STAR: `Situation`, `Task`, `Action`, `Result`
- GCDIO: `Goal`, `Constraints`, `Decision`, `Implementation`, `Outcome`

## Create Session

`POST /api/v2/structure-practice/session`

Request:

```json
{
  "track": "JavaScript Developer",
  "difficulty": "easy"
}
```

Response shape:

```json
{
  "practiceId": 6,
  "interviewId": 313,
  "track": "JavaScript Developer",
  "questions": [
    {
      "text": "Explain closures in JavaScript and provide a practical use case.",
      "index": 0,
      "question_id": 1231,
      "structure_hint": "Use C-T-E-T-D...",
      "framework": "C-T-E-T-D",
      "sections": ["Context", "Theory", "Example", "Trade-offs", "Decision"],
      "current_section": "Context",
      "current_hint": "Start with Context..."
    }
  ],
  "status": "active",
  "createdAt": "2026-01-15T18:00:00Z"
}
```

## Submit Section Audio

`POST /api/v2/structure-practice/{practice_id}/question/{question_index}/section/{section_name}/submit`

Multipart form fields:

- `file`: audio file
- `language`: optional, defaults to `en`
- `time_spent_seconds`: optional integer

Example:

```bash
curl -X POST "http://localhost:8000/api/v2/structure-practice/6/question/0/section/Context/submit" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@context_answer.mp3" \
  -F "language=en" \
  -F "time_spent_seconds=30"
```

Response shape:

```json
{
  "answerId": 1,
  "practiceId": 6,
  "questionIndex": 0,
  "sectionName": "Context",
  "sectionsComplete": 1,
  "totalSections": 5,
  "nextSection": "Theory",
  "nextSectionHint": "Great start! Now move to Theory...",
  "isComplete": false,
  "message": "Section 'Context' recorded successfully (whisper-1, 5432ms). Continue to Theory."
}
```

Submit sections in framework order. The backend validates section names against the framework.

## Analyze Submitted Sections

`POST /api/v2/structure-practice/{practice_id}/question/{question_index}/analyze`

Analysis can run after all sections are submitted or while the answer is incomplete. Missing sections are returned with `status="missing"`.

Response shape:

```json
{
  "answerId": 5,
  "practiceId": 6,
  "questionIndex": 0,
  "frameworkProgress": {
    "frameworkName": "C-T-E-T-D",
    "sections": [
      {
        "name": "Context",
        "status": "complete",
        "answerRecorded": true,
        "timeSpentSeconds": 30
      }
    ],
    "completionPercentage": 90,
    "sectionsComplete": 5,
    "totalSections": 5,
    "progressMessage": "Strong answer structure..."
  },
  "timePerSection": [
    {"sectionName": "Context", "seconds": 30}
  ],
  "keyInsight": "Add more detail on trade-offs.",
  "analyzedAt": "2026-01-15T18:15:00Z",
  "llmModel": "gpt-4o-mini",
  "llmLatencyMs": 4200
}
```

## Implementation Files

- `src/api/routes/interviews_v2.py`: session, section submit, and analysis endpoints
- `src/models/schemas/structure_practice.py`: request and response schemas
- `src/models/db/structure_practice.py`: practice session and answer models
- `src/repository/crud/structure_practice.py`: structure-practice persistence
- `src/services/progressive_hints.py`: framework detection, sections, and next-section hints
- `src/services/structure_analysis.py`: LLM-backed analysis of combined section answers
- `src/services/audio_processor.py`: audio validation and temporary-file handling
- `src/services/whisper.py`: Whisper transcription

## Rules

- Requires authentication.
- The practice session must belong to the authenticated user.
- Supported audio formats are MP3, WAV, M4A, and FLAC.
- One section submission stores one `StructurePracticeAnswer`.
- Re-analysis reads all submitted sections for the target question and combines them in framework order.
