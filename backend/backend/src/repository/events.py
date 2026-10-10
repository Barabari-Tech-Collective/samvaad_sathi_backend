import fastapi
import loguru
import sqlalchemy
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSessionTransaction
from sqlalchemy.pool.base import _ConnectionRecord

from src.repository.database import async_db
from src.repository.table import Base
import src.models.db  # noqa: F401  # Ensure all ORM models are imported before metadata operations


@event.listens_for(target=async_db.async_engine.sync_engine, identifier="connect")
def inspect_db_server_on_connection(
    db_api_connection: AsyncAdapt_asyncpg_connection, connection_record: _ConnectionRecord
) -> None:
    loguru.logger.info(f"New DB API Connection ---\n {db_api_connection}")
    loguru.logger.info(f"Connection Record ---\n {connection_record}")


@event.listens_for(target=async_db.async_engine.sync_engine, identifier="close")
def inspect_db_server_on_close(
    db_api_connection: AsyncAdapt_asyncpg_connection, connection_record: _ConnectionRecord
) -> None:
    loguru.logger.info(f"Closing DB API Connection ---\n {db_api_connection}")
    loguru.logger.info(f"Closed Connection Record ---\n {connection_record}")


async def initialize_db_tables(connection: AsyncConnection) -> None:
    """
    Initialize database tables safely without dropping existing data.
    Uses CREATE TABLE IF NOT EXISTS approach for backward compatibility.
    For production, use Alembic migrations instead.
    """
    loguru.logger.info("Database Table Initialization --- Starting . . .")

    try:
        # Check if alembic_version table exists to determine if migrations are being used
        result = await connection.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version')")
        )
        has_alembic_version = result.scalar()
        
        if has_alembic_version:
            loguru.logger.info("Database managed by Alembic migrations - skipping table creation")
            loguru.logger.info("To initialize/update schema, run: alembic upgrade head")
        else:
            # Create tables only if they don't exist (backward compatibility mode)
            loguru.logger.warning("No Alembic version table found - using fallback table creation")
            loguru.logger.warning("Consider running 'alembic upgrade head' for proper schema management")
            
            # Use CREATE TABLE IF NOT EXISTS equivalent for SQLAlchemy
            await connection.run_sync(Base.metadata.create_all, checkfirst=True)
            
            loguru.logger.info("Database tables created (if not exists)")

        # Backward compatibility patching for old backend schemas.
        # This is intentionally safe/idempotent and keeps old DBs runnable even before migrations are applied.
        dialect = connection.dialect.name
        if dialect == "postgresql":
            await connection.execute(text("ALTER TABLE interview ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ"))
            await connection.execute(text("ALTER TABLE interview ADD COLUMN IF NOT EXISTS duration_seconds INTEGER"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_interview_completed_at ON interview (completed_at)"))

            # interview.difficulty and interview.job_profile_id are declared on the
            # model but no migration ever adds them: the original create_table has
            # neither, and the only other reference is an alter_column that assumes
            # difficulty already exists. A database created before they were added to
            # the model therefore never gets them, and create_all(checkfirst=True)
            # cannot help because it only creates missing tables, never columns.
            # Every interview query selects both, so the whole product 500s - the
            # same failure mode, and the same fix, as user.is_admin below.
            # NOT NULL DEFAULT 'medium' matches the model default and leaves existing
            # rows valid.
            await connection.execute(
                text(
                    "ALTER TABLE interview ADD COLUMN IF NOT EXISTS difficulty "
                    "VARCHAR(16) NOT NULL DEFAULT 'medium'"
                )
            )
            await connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_interview_difficulty ON interview (difficulty)")
            )
            await connection.execute(
                text("ALTER TABLE interview ADD COLUMN IF NOT EXISTS job_profile_id INTEGER")
            )
            await connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_interview_job_profile_id ON interview (job_profile_id)")
            )

            # user.is_admin gates the cross-student analytics endpoints. The model
            # declares it, so if the column is absent EVERY query against `user`
            # fails - including login - which is exactly what happened on staging
            # when the code deployed without its Alembic migration being applied.
            # Environments that can run `alembic upgrade head` get the column from
            # the migration and this is a no-op; environments that cannot (Render's
            # free tier has no Shell, One-Off Jobs or Pre-Deploy Command) still come
            # up healthy. Matches the ALTER above in intent and idempotency: the
            # DEFAULT false means existing rows are non-admin, which is the safe
            # direction for an authorization flag.
            await connection.execute(
                text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false')
            )

            # Composite indexes backing the two hottest interview queries
            # (active-interview lookup, history pagination). Same rationale: the
            # migration is authoritative, this keeps un-migrated environments fast
            # rather than silently degraded.
            await connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_interview_user_id_status_id ON interview (user_id, status, id)")
            )
            await connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_interview_user_id_id ON interview (user_id, id)")
            )

            await connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS analytics_event (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NULL REFERENCES \"user\"(id) ON DELETE SET NULL,
                        interview_id INTEGER NULL REFERENCES interview(id) ON DELETE SET NULL,
                        event_type VARCHAR(64) NOT NULL,
                        event_data JSONB NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_user_id ON analytics_event (user_id)"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_interview_id ON analytics_event (interview_id)"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_event_type ON analytics_event (event_type)"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_created_at ON analytics_event (created_at)"))
        elif dialect == "sqlite":
            await connection.execute(text("CREATE TABLE IF NOT EXISTS analytics_event (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NULL, interview_id INTEGER NULL, event_type VARCHAR(64) NOT NULL, event_data TEXT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_user_id ON analytics_event (user_id)"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_interview_id ON analytics_event (interview_id)"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_event_type ON analytics_event (event_type)"))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analytics_event_created_at ON analytics_event (created_at)"))

        await report_schema_drift(connection)

    except Exception as e:
        loguru.logger.error(f"Database initialization error: {e}")
        raise

    loguru.logger.info("Database Table Initialization --- Successfully Completed!")


def _missing_columns(inspector, metadata) -> dict[str, list[str]]:
    """Columns the models declare that the live database does not have."""
    present_tables = set(inspector.get_table_names())
    drift: dict[str, list[str]] = {}
    for name, table in metadata.tables.items():
        if name not in present_tables:
            # A missing table is a different problem and is reported elsewhere;
            # flagging all of its columns here would only add noise.
            continue
        actual = {column["name"] for column in inspector.get_columns(name)}
        missing = [column.name for column in table.columns if column.name not in actual]
        if missing:
            drift[name] = missing
    return drift


async def report_schema_drift(connection) -> None:
    """Names columns the models expect but the database lacks.

    A column present on the model and absent from the table makes every query
    touching that table fail at runtime, because SQLAlchemy names each column in
    its SELECT. That has already cost this product twice: user.is_admin broke
    login on staging, and interview.difficulty broke interview creation, each
    surfacing only as an opaque 500 long after deploy. Alembic cannot be relied
    on to prevent it - no migration adds interview.difficulty at all - and
    create_all(checkfirst=True) only ever creates missing tables, never columns.

    Reported rather than raised: refusing to start would turn drift into an
    outage, while a named list in the logs is immediately actionable.
    """
    try:
        drift = await connection.run_sync(
            lambda sync_connection: _missing_columns(
                sqlalchemy.inspect(sync_connection), Base.metadata
            )
        )
    except Exception as inspection_error:  # noqa: BLE001
        loguru.logger.warning(f"Could not check schema drift: {inspection_error}")
        return

    if not drift:
        loguru.logger.info("Schema check --- models and database agree")
        return

    for table, columns in sorted(drift.items()):
        loguru.logger.error(
            "SCHEMA DRIFT | table '{}' is missing column(s) declared on the model: {}. "
            "Every query selecting them will fail with UndefinedColumnError.",
            table,
            ", ".join(sorted(columns)),
        )


async def initialize_db_connection(backend_app: fastapi.FastAPI) -> None:
    loguru.logger.info("Database Connection --- Establishing . . .")

    backend_app.state.db = async_db

    async with backend_app.state.db.async_engine.begin() as connection:
        await initialize_db_tables(connection=connection)

    loguru.logger.info("Database Connection --- Successfully Established!")


async def dispose_db_connection(backend_app: fastapi.FastAPI) -> None:
    loguru.logger.info("Database Connection --- Disposing . . .")

    await backend_app.state.db.async_engine.dispose()

    loguru.logger.info("Database Connection --- Successfully Disposed!")
