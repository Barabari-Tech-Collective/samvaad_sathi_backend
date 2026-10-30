import contextlib
import typing

import fastapi
from sqlalchemy.ext.asyncio import (
    async_sessionmaker as sqlalchemy_async_sessionmaker,
    AsyncSession as SQLAlchemyAsyncSession,
    AsyncSessionTransaction as SQLAlchemyAsyncSessionTransaction,
)

from src.repository.database import async_db
from src.utilities.server_timing import enabled as server_timing_enabled, step


async def get_async_session() -> typing.AsyncGenerator[SQLAlchemyAsyncSession, None]:
    """
    Dependency that provides a database session for each request.
    Each request gets its own session instance for better concurrency.
    """
    # Get a new session instance for this request
    session = async_db.get_session()
    try:
        if server_timing_enabled():
            # Getting a connection includes the pool's ping and, when the pool is
            # empty, a brand-new TLS connection to the database. Timed apart from queries.
            with step("db_connect"):
                await session.connection()
        yield session
    except Exception as e:
        print(f"Database session error: {e}")
        await session.rollback()
        raise  # Re-raise the exception so FastAPI can handle it properly
    finally:
        await session.close()
