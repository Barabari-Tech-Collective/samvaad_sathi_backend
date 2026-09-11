import fastapi
import loguru
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

    except Exception as e:
        loguru.logger.error(f"Database initialization error: {e}")
        raise

    loguru.logger.info("Database Table Initialization --- Successfully Completed!")


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
