# Development Guide

This guide describes how to work on the current Samvaad Sathi backend. It intentionally omits the original template workflow and only documents what this repository uses now.

## Prerequisites

- Python with `pip`
- PostgreSQL, either managed or local through `backend/docker-compose.local.yml`
- Docker, if using the containerized API
- OpenAI API key for LLM, Whisper, and pronunciation TTS features
- ElevenLabs API key only if testing `/api/tts/convert`

Note: `pyproject.toml` declares Python `>=3.13`, while the Docker image currently uses `python:3.12-slim` and installs `requirements.txt`. Align these before a production release if package metadata will be enforced.

## Environment

The settings class loads `backend/.env`. Docker Compose in `backend/backend/docker-compose.yml` also points to `../.env`.

Minimum development values:

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
MAX_AUDIO_SIZE_MB=25
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=hpp4J3VqNfWAUOO0d1Us
```

## Local Workflow

Start PostgreSQL:

```bash
docker compose -f backend/docker-compose.local.yml up -d postgres
```

Install runtime dependencies and run migrations:

```bash
cd backend/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

Run the API:

```bash
python3 -m uvicorn src.main:backend_app --reload
```

Swagger UI: `http://127.0.0.1:8000/docs`

## Docker Workflow

```bash
cd backend/backend
docker compose up -d --build
docker compose logs -f api
```

The Docker API service loads `backend/.env`, exposes port `8000`, and uses `uvicorn src.main:backend_app --host 0.0.0.0 --port 8000`.

## Database Rules

- Use Alembic for schema changes: update models, generate a revision, review it, then run `alembic upgrade head`.
- Do not rely on startup table creation for normal development. `src/repository/events.py` only uses `create_all(checkfirst=True)` when no `alembic_version` table exists.
- Do not run destructive database reset commands against shared or production databases.
- The app uses `src/repository/database.py` for the active async engine and session factory.
- `src/repository/supabase_database.py` is not wired into app startup.

Common commands:

```bash
cd backend/backend
python3 scripts/db_manager.py status
python3 scripts/db_manager.py migrate
alembic current
alembic heads
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Auth Rules

- Most business routes depend on `get_current_user` and require `Authorization: Bearer <token>`.
- Local registration/login returns both access and refresh tokens.
- Refresh tokens are stored in the `session` table and rotated by `/api/auth/cognito/refresh`.
- Cognito login is optional. It creates or finds a local `User`, then mints the app's normal JWT.

## API Rules

- Every route is mounted under `/api`.
- New frontend work should use V2 routes where available:
  - `/api/v2/interviews/*`
  - `/api/v2/summary-report`
  - `/api/v2/analytics/*`
  - `/api/v2/job-profiles`
- V1 routes are still supported for existing clients.
- `/api/final-report` is legacy; use summary report endpoints for current reporting.
- Route ownership checks matter: never expose an interview, question attempt, practice session, or report without validating the authenticated user.

## Audio and AI Rules

- Audio formats: MP3, WAV, M4A, FLAC.
- Audio size limit: 25 MB in `audio_processor.py`; duration cap: 10 minutes.
- Audio files are written to temporary files for processing and deleted after use.
- The database stores transcription JSON and a generated reference name, not durable audio storage.
- OpenAI failures should return structured errors or fallback content when the calling service supports it.
- Easy interview difficulty uses static questions; medium/hard/expert use LLM generation with fallback.

## Tests

Run automated tests:

```bash
cd backend/backend
pytest
```

Run smoke tests against a live API:

```bash
python3 scripts/run_all_smoke.py
python3 scripts/smoke_test.py
python3 scripts/smoke_v2_follow_up.py
python3 scripts/smoke_structure_practice.py
python3 scripts/smoke_pacing_practice.py
python3 scripts/smoke_analytics.py
```

Set `SMOKE_BASE_URL` and `SMOKE_API_PREFIX` to target non-default hosts.

## Documentation Rules

- Keep endpoint docs synchronized with route decorators in `src/api/routes`.
- Keep file-purpose docs synchronized with `backend/docs/SOURCE_FILE_GUIDE.md`.
- Mark compatibility code as legacy instead of presenting it as the recommended path.
- Do not document planned object storage, fine-tuning, or training systems as live behavior.
