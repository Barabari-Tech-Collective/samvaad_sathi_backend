# Samvaad Sathi Backend

FastAPI backend for AI-assisted interview practice. It handles user auth, resume parsing, interview question generation, audio transcription, analysis, reports, speech pacing, pronunciation practice, structure practice, job profiles, and analytics.

## Current Stack

- Runtime: FastAPI, Uvicorn, async SQLAlchemy, Alembic
- Database: PostgreSQL through `asyncpg`
- Auth: local JWT plus refresh-token sessions; optional Cognito OIDC login
- AI services: OpenAI for LLM, Whisper transcription, and pronunciation TTS; ElevenLabs for text-to-speech
- Packaging: `requirements.txt` for Docker/runtime, `pyproject.toml` for project metadata and dev tooling

## Repository Layout

- `src/main.py`: FastAPI app initialization, middleware, startup/shutdown handlers, root route
- `src/api/routes/`: HTTP route modules
- `src/services/`: business logic and AI integrations
- `src/models/db/`: SQLAlchemy ORM models
- `src/models/schemas/`: Pydantic request/response models
- `src/repository/`: database engine, Alembic, and CRUD repositories
- `scripts/`: smoke tests and database utilities
- `tests/`: unit and integration tests
- `backend/docs/SOURCE_FILE_GUIDE.md`: detailed source-file map

## Setup

The app reads environment from `backend/.env` when run from this repository. Docker Compose at `backend/backend/docker-compose.yml` also loads `../.env`, which is the same file.

Required local values:

```env
ENVIRONMENT=DEV
BACKEND_SERVER_HOST=127.0.0.1
BACKEND_SERVER_PORT=8000
BACKEND_SERVER_WORKERS=1

POSTGRES_SCHEMA=postgresql
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=defaultdb
POSTGRES_USERNAME=postgres
POSTGRES_PASSWORD=postgres
DB_TIMEOUT=30
DB_POOL_SIZE=5
DB_MAX_POOL_CON=5
DB_POOL_OVERFLOW=10
IS_DB_ECHO_LOG=False
IS_DB_EXPIRE_ON_COMMIT=False
IS_DB_FORCE_ROLLBACK=False

IS_ALLOWED_CREDENTIALS=True
API_TOKEN=dev-api-token
AUTH_TOKEN=dev-auth-token
JWT_TOKEN_PREFIX=Bearer
JWT_SECRET_KEY=change_this_dev_secret
JWT_SUBJECT=access
JWT_ALGORITHM=HS256
JWT_MIN=1
JWT_HOUR=60
JWT_DAY=1
REFRESH_TOKEN_EXPIRY_MINUTES=43200

HASHING_ALGORITHM_LAYER_1=bcrypt
HASHING_ALGORITHM_LAYER_2=argon2
HASHING_SALT=change_this_salt

SESSION_SECRET_KEY=change_this_session_secret
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=150
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=hpp4J3VqNfWAUOO0d1Us
MAX_AUDIO_SIZE_MB=25
```

Optional Cognito values:

```env
COGNITO_REGION=ap-south-1
COGNITO_USERPOOL_ID=
COGNITO_CLIENT_ID=
COGNITO_CLIENT_SECRET=
COGNITO_SCOPES=openid email phone profile
COGNITO_HOSTED_UI_DOMAIN=
COGNITO_POST_LOGIN_REDIRECT_URL=
COGNITO_POST_LOGOUT_REDIRECT_URL=
```

## Run Locally

Start a local PostgreSQL database:

```bash
docker compose -f backend/docker-compose.local.yml up -d postgres
```

Run migrations:

```bash
cd backend/backend
alembic upgrade head
```

Start the API:

```bash
cd backend/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn src.main:backend_app --reload
```

Swagger UI: `http://127.0.0.1:8000/docs`

## Run with Docker

```bash
cd backend/backend
docker compose up -d --build
docker compose logs -f api
```

The API is exposed at `http://localhost:8000/docs`.

## API Surface

All routes are mounted under `/api`.

### Auth and Users

- `POST /api/users`: register a local user; returns access and refresh tokens
- `POST /api/login`: local login
- `GET /api/me`: current authenticated user
- `PUT /api/users/profile`: update onboarding/profile fields
- `GET /api/auth/cognito/login`: start Cognito login
- `GET /api/auth/cognito/authorize`: Cognito callback
- `GET /api/auth/cognito/logout`: clear local session and optionally Cognito Hosted UI session
- `GET /api/auth/cognito/session`: read signed Starlette session user
- `GET /api/auth/cognito/jwt`: read JWT stored in signed Starlette session
- `POST /api/auth/cognito/refresh`: rotate app refresh token and return a new access token

### Resume

- `POST /api/extract-resume`: upload PDF or text resume, extract text, skills, and experience, then persist on the authenticated user
- `GET /api/me/resume`: return the authenticated user's stored resume data
- `GET /api/get_knowledgeset`: return normalized skill set derived from stored resume content

### Interviews

- `POST /api/interviews/create`: create or resume active V1 interview
- `POST /api/interviews/generate-questions`: generate or return persisted V1 questions
- `POST /api/interviews/complete`: mark an interview complete
- `GET /api/interviews`: list authenticated user's interviews
- `GET /api/interviews/{interview_id}/questions`: list persisted questions
- `GET /api/interviews/{interview_id}/question-attempts`: list question attempts
- `POST /api/interviews/question-attempts`: create a question attempt
- `GET /api/interviews-with-summary`: list interviews with summary report fields
- `POST /api/interviews/resume`: resume an existing interview

### Interviews V2 and Practice

- `POST /api/v2/interviews/create`: create or resume V2 interview
- `POST /api/v2/interviews/generate-questions`: generate adaptive 5-question set with attempts and supplements
- `POST /api/v2/interviews/non-tech/generate-questions`: generate non-technical blueprint questions
- `POST /api/v2/interviews/structure-practice`: return questions with structure hints
- `POST /api/v2/interviews/{interview_id}/supplements`: generate supplements for interview questions
- `GET /api/v2/interviews/{interview_id}/supplements`: read supplements
- `POST /api/v2/pronunciation/create`: create pronunciation practice
- `GET /api/v2/pronunciation/{practice_id}/audio/{question_number}`: generate pronunciation audio
- `POST /api/v2/structure-practice/session`: create section-by-section structure practice session
- `POST /api/v2/structure-practice/{practice_id}/question/{question_index}/section/{section_name}/submit`: submit one section audio
- `POST /api/v2/structure-practice/{practice_id}/question/{question_index}/analyze`: analyze submitted sections
- `GET /api/pacing-practice/has-practiced`: pacing-practice status
- `GET /api/pacing-practice/levels`: pacing prompt levels
- `POST /api/pacing-practice/session`: create pacing session
- `POST /api/pacing-practice/session/{session_id}/submit`: submit pacing answer audio
- `GET /api/pacing-practice/session/{session_id}`: read pacing session

### Audio, Analysis, Reports, TTS

- `POST /api/transcribe-whisper`: validate audio, transcribe with Whisper, persist transcription on a question attempt, and possibly generate a follow-up
- `POST /api/complete-analysis`: aggregate selected analyses for a question attempt
- `POST /api/domain-base-analysis`: LLM domain analysis
- `POST /api/communication-based-analysis`: LLM communication analysis
- `POST /api/analyze-pace`: pace analysis
- `POST /api/analyze-pause`: pause analysis
- `POST /api/final-report`: legacy final report
- `GET /api/final-report/{interview_id}`: read legacy final report
- `POST /api/summary-report`: V1 summary report
- `GET /api/summary-report/{interview_id}`: read V1 summary report
- `GET /api/summary-reports`: list V1 summary reports
- `POST /api/v2/summary-report`: V2 summary report
- `GET /api/v2/summary-report/{interview_id}`: read V2 summary report
- `POST /api/tts/convert`: ElevenLabs text-to-speech conversion

### Job Profiles and Analytics

- `POST /api/v2/job-profiles`: create a job profile
- `GET /api/v2/job-profiles`: list job profiles
- `DELETE /api/v2/job-profiles/{job_profile_id}`: delete a job profile
- `GET /api/analytics/*`: legacy analytics views
- `GET /api/v2/analytics/*`: dashboard, student, college, interview, ranking, role, difficulty, question, dropoff, and insight analytics

## Current Rules

- Protected endpoints require `Authorization: Bearer <access_token>`.
- New client work should prefer V2 interview, structure-practice, summary-report, analytics, and job-profile endpoints.
- V1 interview/report endpoints remain available for compatibility.
- Database schema changes must go through Alembic migrations. Startup has an idempotent fallback for older databases, but it is not the primary schema-management path.
- Audio uploads are processed through temporary files only. The database stores a generated reference name plus transcription metadata, not a durable audio object.
- Question attempts and practice sessions must be checked against the authenticated user before reading or writing.
- Keep secrets out of committed files. Use `backend/.env`, Docker secrets, or deployment environment variables.

## Deprecated or Legacy

- `backend/TEMPLATE_README.md` is an archive of the original FastAPI template. Do not use it for setup.
- Legacy `Account` template endpoints/models are no longer part of the current route surface.
- `src/repository/supabase_database.py` is a compatibility helper. The running app uses `src/repository/database.py`.
- `/api/final-report` is a legacy report format. Prefer `/api/v2/summary-report` for new consumers.
- The old object-storage and model-training plan is not implemented. See `backend/docs/data_architecture_and_training.md` for the current-vs-planned boundary.

## Tests and Utilities

Run the full smoke suite against a running API:

```bash
cd backend/backend
python3 scripts/run_all_smoke.py
```

Useful targeted scripts:

```bash
python3 scripts/smoke_test.py
python3 scripts/smoke_v2_follow_up.py
python3 scripts/smoke_structure_practice.py
python3 scripts/smoke_pacing_practice.py
python3 scripts/smoke_analytics.py
python3 scripts/db_manager.py status
python3 scripts/db_manager.py migrate
```

Run automated tests:

```bash
pytest
```

## More Docs

- `backend/docs/DEV_GUIDE.md`: development workflow and rules
- `backend/docs/architecture.md`: current architecture
- `backend/docs/SOURCE_FILE_GUIDE.md`: file-by-file source guide
- `backend/backend/STRUCTURE_PRACTICE_API.md`: section-by-section structure practice API
- `backend/backend/AUDIO_TRANSCRIPTION.md`: Whisper transcription behavior
- `backend/backend/DATABASE_PERSISTENCE.md`: database persistence and migrations
