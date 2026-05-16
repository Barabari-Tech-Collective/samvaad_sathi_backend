# Database Persistence

The backend uses PostgreSQL through async SQLAlchemy. Data is persistent across application restarts. Schema changes should be managed through Alembic.

## Active Database Path

- Active engine: `src/repository/database.py`
- Session dependency: `src/api/dependencies/session.py`
- Repository dependency: `src/api/dependencies/repository.py`
- Startup initialization: `src/repository/events.py`
- Alembic config: `alembic.ini`
- Migrations: `src/repository/migrations/versions/`

`src/repository/supabase_database.py` exists as a compatibility helper but is not the engine used by application startup.

## Startup Behavior

On startup, `initialize_db_tables()` checks whether the `alembic_version` table exists.

- If Alembic is present, startup skips table creation and expects migrations to manage schema.
- If Alembic is absent, startup falls back to `Base.metadata.create_all(checkfirst=True)` for compatibility.
- Startup also applies a small idempotent compatibility patch for older schemas around interview completion fields and `analytics_event`.

It does not drop tables.

## Required Environment

```env
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
```

The active engine builds a `postgresql+asyncpg://...` URI and passes an SSL context to asyncpg. Certificate verification is currently disabled in code for managed-Postgres compatibility; revisit this before strict production hardening.

## Commands

```bash
cd backend/backend
python3 scripts/db_manager.py status
python3 scripts/db_manager.py migrate
alembic current
alembic heads
alembic history
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

Development-only reset:

```bash
python3 scripts/db_manager.py reset
```

Do not run reset against shared or production databases.

## Migration Rules

- Update SQLAlchemy models first.
- Generate an Alembic revision.
- Review generated operations before applying them.
- Keep migrations idempotent where possible.
- Do not use startup fallback table creation as a substitute for migrations.
- When adding a model, make sure it is imported where metadata discovery needs it.

## Concurrency

`AsyncDatabase` uses an async session factory. Request dependencies should create isolated sessions and close them after use. Repositories should not share one long-lived session across requests.
