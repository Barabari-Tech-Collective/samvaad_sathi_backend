# Source File Guide

This guide explains where the current backend behavior lives. It is organized by directory so developers can move quickly from an API issue to the right model, repository, or service.

## Application and Configuration

| File | Purpose |
| --- | --- |
| `src/main.py` | Creates the FastAPI app, adds CORS and session middleware, registers startup/shutdown events, mounts `/api`, and exposes `/`. |
| `src/api/endpoints.py` | Central router registry and `/api/health`. |
| `src/config/manager.py` | Selects settings for the configured environment. |
| `src/config/events.py` | Builds startup and shutdown handlers. |
| `src/config/settings/base.py` | Core environment variables, CORS, auth, DB, OpenAI, Cognito, and TTS settings. |
| `src/config/settings/development.py` | Development settings override. |
| `src/config/settings/staging.py` | Staging settings override. |
| `src/config/settings/production.py` | Production settings override. |
| `src/config/settings/environment.py` | Environment selection helpers. |

## API Dependencies

| File | Purpose |
| --- | --- |
| `src/api/dependencies/auth.py` | Bearer-token validation and current-user loading. |
| `src/api/dependencies/repository.py` | Creates repository dependencies backed by request DB sessions. |
| `src/api/dependencies/session.py` | Provides async SQLAlchemy sessions to routes/services. |

## API Routes

| File | Purpose |
| --- | --- |
| `src/api/routes/users.py` | Local registration, login, `/me`, profile update, refresh-session creation, signup analytics. |
| `src/api/routes/auth_cognito.py` | Optional Cognito OIDC login, callback, logout, signed session reads, refresh-token rotation. |
| `src/api/routes/resume.py` | Resume upload, PDF/text extraction, LLM resume entity extraction, stored resume reads, knowledgeset. |
| `src/api/routes/interviews.py` | V1 interview creation, question generation, question/attempt listing, completion, resume flow. |
| `src/api/routes/interviews_v2.py` | V2 interviews, non-tech question blueprint, supplements, pronunciation practice, structure practice. |
| `src/api/routes/audio.py` | Whisper transcription for question attempts and follow-up triggering. |
| `src/api/routes/analysis.py` | Complete, domain, communication, pace, and pause analyses for question attempts. |
| `src/api/routes/report.py` | Legacy final-report generation and retrieval. |
| `src/api/routes/summary_report.py` | V1 summary report generation, retrieval, and list endpoints. |
| `src/api/routes/summary_report_v2.py` | V2 summary report generation and retrieval. |
| `src/api/routes/tts.py` | ElevenLabs text-to-speech conversion endpoint. |
| `src/api/routes/speech_pacing.py` | Pacing-practice session creation, submission, scoring, and read endpoints. |
| `src/api/routes/analytics.py` | Legacy analytics endpoints. |
| `src/api/routes/analytics_v2.py` | Dashboard, student, college, interview, ranking, role, difficulty, question, dropoff, and insight analytics. |
| `src/api/routes/job_profiles.py` | V2 job-profile CRUD endpoints. |

## Services

| File | Purpose |
| --- | --- |
| `src/services/llm.py` | OpenAI client calls for resume extraction, question generation, domain analysis, and communication analysis. |
| `src/services/whisper.py` | OpenAI Whisper transcription and transcription cleanup helpers. |
| `src/services/audio_processor.py` | Audio validation, duration estimates, temporary-file lifecycle, and simple energy features. |
| `src/services/follow_up.py` | Generates follow-up questions after transcription using persisted question/answer context. |
| `src/services/question_supplements.py` | Generates and serializes code/diagram supplements for interview questions. |
| `src/services/static_questions.py` | Static fallback/easy-difficulty interview questions. |
| `src/services/non_tech_blueprint.py` | Fixed non-technical question blueprint selection. |
| `src/services/syllabus_service.py` | Role derivation, topic selection, and question-ratio logic. |
| `src/services/syllabus.py` | Syllabus domain types and helpers. |
| `src/services/syllabus_data.py` | Syllabus data definitions. |
| `src/services/syllabus_content.py` | Topic content used by syllabus logic. |
| `src/services/syllabus_examples.py` | Example prompts/content for syllabus-backed generation. |
| `src/services/analysis.py` | Aggregates domain, communication, pace, and pause analyses. |
| `src/services/pace_analysis.py` | Pace feedback calculations. |
| `src/services/pause_analysis.py` | Pause analysis from transcription timing. |
| `src/services/structure_hints.py` | LLM structure hints for interview questions. |
| `src/services/progressive_hints.py` | Framework detection and section-by-section hint progression. |
| `src/services/structure_analysis.py` | Section-by-section structure-practice analysis. |
| `src/services/report.py` | Legacy final-report service. |
| `src/services/summary_report.py` | V1 summary report generation. |
| `src/services/summary_report_v2.py` | V2 summary report generation. |
| `src/services/analytics.py` | Aggregation logic for analytics endpoints. |
| `src/services/analytics_events.py` | Event tracking writes for signup, role selection, interview lifecycle, and engagement. |
| `src/services/pacing_practice_service.py` | Pacing-practice prompt/session helpers. |
| `src/services/pronunciation_tts.py` | OpenAI pronunciation-audio generation. |
| `src/services/elevenlabs_tts.py` | ElevenLabs text-to-speech integration. |

## Database Models

| File | Purpose |
| --- | --- |
| `src/models/db/user.py` | User profile, resume fields, onboarding fields, and target-position enum. |
| `src/models/db/session.py` | Refresh-token session records. |
| `src/models/db/interview.py` | Interview session records. |
| `src/models/db/interview_question.py` | Persisted interview questions and follow-up metadata. |
| `src/models/db/question_attempt.py` | Attempts, transcription JSON, audio reference, and analysis JSON. |
| `src/models/db/question_supplement.py` | Code/diagram supplements for questions. |
| `src/models/db/report.py` | Legacy final report table. |
| `src/models/db/summary_report.py` | Summary report table. |
| `src/models/db/analytics_event.py` | Analytics event log. |
| `src/models/db/job_profile.py` | Job profile definitions. |
| `src/models/db/pacing_practice.py` | Pacing-practice sessions. |
| `src/models/db/pronunciation_practice.py` | Pronunciation-practice sessions. |
| `src/models/db/structure_practice.py` | Structure-practice sessions and section answers. |
| `src/models/db/__init__.py` | Imports core ORM models for metadata registration. |

## Schemas

| File | Purpose |
| --- | --- |
| `src/models/schemas/base.py` | Shared Pydantic base model. |
| `src/models/schemas/user.py` | User auth, token, profile, and response schemas. |
| `src/models/schemas/jwt.py` | JWT schema definitions. |
| `src/models/schemas/resume.py` | Resume extraction and knowledgeset response schemas. |
| `src/models/schemas/interview.py` | Interview, question, supplement, attempt, and list schemas. |
| `src/models/schemas/audio.py` | Audio transcription response schema. |
| `src/models/schemas/analysis.py` | Complete, domain, communication, pace, and pause analysis schemas. |
| `src/models/schemas/report.py` | Legacy report schemas. |
| `src/models/schemas/summary_report.py` | V1 summary report schemas. |
| `src/models/schemas/summary_report_v2.py` | V2 summary report schemas. |
| `src/models/schemas/analytics.py` | Legacy analytics schemas. |
| `src/models/schemas/analytics_v2.py` | V2 dashboard analytics schemas. |
| `src/models/schemas/job_profile.py` | Job-profile schemas. |
| `src/models/schemas/pacing_practice.py` | Pacing-practice schemas. |
| `src/models/schemas/pronunciation.py` | Pronunciation-practice schemas. |
| `src/models/schemas/structure_practice.py` | Structure-practice schemas. |

## Repository Layer

| File | Purpose |
| --- | --- |
| `src/repository/database.py` | Active async PostgreSQL engine, connection pool, and session factory. |
| `src/repository/supabase_database.py` | Compatibility helper; not wired into app startup. |
| `src/repository/aurora_database.py` | Aurora-specific helper/experiment. |
| `src/repository/events.py` | Database startup/shutdown and compatibility initialization. |
| `src/repository/table.py` | Declarative SQLAlchemy base. |
| `src/repository/base.py` | Model imports for metadata discovery. |
| `src/repository/crud/base.py` | Shared CRUD base behavior. |
| `src/repository/crud/user.py` | User create, lookup, password verification, profile updates, onboarding. |
| `src/repository/crud/session.py` | Refresh-session create, lookup, delete. |
| `src/repository/crud/interview.py` | Interview create, list, lookup, resume, complete. |
| `src/repository/crud/interview_question.py` | Interview-question create/list/update helpers. |
| `src/repository/crud/question.py` | Question-attempt create/list/update/transcription helpers. |
| `src/repository/crud/question_supplement.py` | Supplement persistence. |
| `src/repository/crud/report.py` | Legacy report persistence. |
| `src/repository/crud/summary_report.py` | Summary report persistence and counts. |
| `src/repository/crud/job_profile.py` | Job-profile persistence. |
| `src/repository/crud/pacing_practice.py` | Pacing-practice persistence. |
| `src/repository/crud/pronunciation_practice.py` | Pronunciation-practice persistence. |
| `src/repository/crud/structure_practice.py` | Structure-practice session and answer persistence. |
| `src/repository/migrations/env.py` | Alembic migration environment. |
| `src/repository/migrations/versions/` | Dated Alembic schema revisions. |

## Security and Utilities

| File | Purpose |
| --- | --- |
| `src/securities/authorizations/jwt.py` | JWT creation and token detail retrieval. |
| `src/securities/hashing/hash.py` | Hashing utility layer. |
| `src/securities/hashing/password.py` | Password hashing and verification. |
| `src/securities/verifications/credentials.py` | Credential verification helpers. |
| `src/utilities/exceptions/*` | Application exception classes. |
| `src/utilities/messages/exceptions/*` | Exception message constants. |
| `src/utilities/formatters/datetime_formatter.py` | Datetime formatting helpers. |
| `src/utilities/formatters/field_formatter.py` | Field formatting helpers. |

## Operational Scripts

| File | Purpose |
| --- | --- |
| `scripts/db_manager.py` | Database status, init, migration, and reset commands. |
| `scripts/create_supabase_db.py` | Supabase database setup helper. |
| `scripts/check_supabase_state.py` | Supabase state inspection helper. |
| `scripts/create_aurora_db.py` | Aurora setup helper. |
| `scripts/check_aurora_state.py` | Aurora state inspection helper. |
| `scripts/run_all_smoke.py` | Runs the smoke suite. |
| `scripts/smoke_*.py` | Targeted smoke tests for auth, interviews, analytics, TTS, pacing, pronunciation, structure practice, security, and misc flows. |
| `scripts/test_*.py` | Script-style verification helpers. |

## Test Files

| File | Purpose |
| --- | --- |
| `tests/unit_tests/test_openapi_schema.py` | OpenAPI/schema sanity checks. |
| `tests/unit_tests/test_resume_schema.py` | Resume schema checks. |
| `tests/unit_tests/test_analytics_service.py` | Analytics service tests. |
| `tests/unit_tests/test_report_service.py` | Legacy report service tests. |
| `tests/unit_tests/test_summary_report_service.py` | Summary report service tests. |
| `tests/integration_tests/test_analytics_v2_contracts.py` | Analytics V2 contract tests. |
