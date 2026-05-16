# System Architecture

Samvaad Sathi is a modular FastAPI service. HTTP routes validate requests and ownership, services perform business logic and AI calls, repositories own database access, and SQLAlchemy models define persisted state.

## System Context

```mermaid
graph TD
    User[Candidate or dashboard user]
    Frontend[Frontend client]
    API[FastAPI backend]
    JWT[Local JWT and refresh sessions]
    Cognito[Optional AWS Cognito OIDC]
    DB[(PostgreSQL)]
    OpenAI[OpenAI LLM, Whisper, TTS]
    Eleven[ElevenLabs TTS]

    User --> Frontend
    Frontend -->|HTTPS REST| API
    API -->|Bearer token validation| JWT
    API -.->|Hosted UI login when configured| Cognito
    API -->|Async SQLAlchemy| DB
    API -->|Question generation, analysis, transcription, pronunciation TTS| OpenAI
    API -->|/api/tts/convert| Eleven
```

## Component Architecture

```mermaid
graph TD
    subgraph API Layer
        Users[users.py]
        Resume[resume.py]
        Interviews[interviews.py and interviews_v2.py]
        Audio[audio.py]
        Analysis[analysis.py]
        Reports[report.py and summary reports]
        Practice[speech_pacing.py and structure/pronunciation routes]
        Analytics[analytics.py and analytics_v2.py]
    end

    subgraph Service Layer
        LLM[llm.py]
        Syllabus[syllabus_service.py and syllabus data]
        Supplements[question_supplements.py]
        FollowUp[follow_up.py]
        AudioProc[audio_processor.py and whisper.py]
        AnalysisSvc[analysis, pace, pause, structure analysis]
        ReportSvc[report and summary report services]
        AnalyticsSvc[analytics and analytics_events]
        TTS[pronunciation_tts.py and elevenlabs_tts.py]
    end

    subgraph Data Layer
        Repos[CRUD repositories]
        Models[SQLAlchemy models]
        Migrations[Alembic migrations]
        Postgres[(PostgreSQL)]
    end

    Users --> Repos
    Resume --> LLM
    Interviews --> Syllabus
    Interviews --> Supplements
    Interviews --> FollowUp
    Audio --> AudioProc
    Analysis --> AnalysisSvc
    Reports --> ReportSvc
    Practice --> TTS
    Practice --> AnalysisSvc
    Analytics --> AnalyticsSvc

    LLM --> OpenAI[OpenAI]
    AudioProc --> OpenAI
    TTS --> OpenAI
    TTS --> ElevenLabs[ElevenLabs]

    Syllabus --> Repos
    Supplements --> Repos
    FollowUp --> Repos
    AnalysisSvc --> Repos
    ReportSvc --> Repos
    AnalyticsSvc --> Repos
    Repos --> Models
    Models --> Migrations
    Repos --> Postgres
```

## Data Flow

1. The client authenticates through local login or optional Cognito login.
2. Protected routes use `get_current_user` to validate the JWT and load the local `User`.
3. Resume upload extracts text from PDF/text files, asks the LLM for structured skills and experience, and stores results on `user`.
4. Interview creation stores an `interview`; question generation stores `interview_question` rows and V2 `question_attempt` rows.
5. Audio submission validates ownership, validates file type/size/duration, sends bytes to Whisper, stores transcription JSON on `question_attempt`, then deletes the temporary file.
6. Analysis endpoints read transcription data, run LLM and rule-based analyses, and persist results in `question_attempt.analysis_json` or report tables.
7. Reporting endpoints synthesize interview-level feedback from questions, attempts, analyses, and summary report records.
8. Analytics endpoints aggregate users, interviews, attempts, reports, and analytics events for dashboard views.

## Current Boundaries

- There is no durable object-storage integration for audio in the running app.
- The current ASR path uses OpenAI Whisper API, not a self-hosted or fine-tuned Whisper model.
- The current app uses PostgreSQL through `src/repository/database.py`.
- Cognito is optional and complements local users; it is not the only authentication path.
- `backend/docs/*.png` files are generated diagram artifacts. Treat Mermaid and Markdown sources as authoritative.
