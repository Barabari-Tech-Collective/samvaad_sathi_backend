"""A small, separate connection pool for the arq worker.

The worker previously touched no database at all. Moving transcription into it
means it needs one, and that pool is spent from the same ceiling the API draws
on - production allows 20 connections with roughly 17 usable.

It is deliberately its own engine rather than the API's. The API pool is sized
for concurrent HTTP requests; the worker's is sized for concurrent *jobs*, a
number we choose. That is the whole reason moving transcription here helps: the
old design held one connection per answering student for the entire duration of
a Whisper call, so fifteen students at once exhausted the pool. Queue depth now
absorbs the load instead, and connection use stays flat.

Keep `WORKER_DB_POOL_SIZE + WORKER_DB_POOL_OVERFLOW` plus the API's
`DB_POOL_SIZE + DB_POOL_OVERFLOW` comfortably under the server's
max_connections, leaving room for migrations and an admin session.
"""

import logging
import ssl

from sqlalchemy.ext.asyncio import (
    async_sessionmaker as sqlalchemy_async_sessionmaker,
    AsyncEngine as SQLAlchemyAsyncEngine,
    AsyncSession as SQLAlchemyAsyncSession,
    create_async_engine as create_sqlalchemy_async_engine,
)

from src.config.manager import settings
from src.repository.database import async_db

logger = logging.getLogger(__name__)

_engine: SQLAlchemyAsyncEngine | None = None
_session_factory: sqlalchemy_async_sessionmaker | None = None


def _build_engine() -> SQLAlchemyAsyncEngine:
    connect_args = {}
    if settings.DB_SSL_ENABLED:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ssl_context

    pool_size = getattr(settings, "WORKER_DB_POOL_SIZE", 2)
    overflow = getattr(settings, "WORKER_DB_POOL_OVERFLOW", 3)
    logger.info(
        "Worker database pool: size=%s overflow=%s (peak %s connections)",
        pool_size,
        overflow,
        pool_size + overflow,
    )
    return create_sqlalchemy_async_engine(
        url=async_db.set_async_db_uri,
        echo=settings.IS_DB_ECHO_LOG,
        pool_size=pool_size,
        max_overflow=overflow,
        pool_timeout=settings.DB_TIMEOUT,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args=connect_args,
    )


def get_session() -> SQLAlchemyAsyncSession:
    """A session on the worker's own pool. Built on first use, not at import,
    so merely importing the worker never opens connections."""
    global _engine, _session_factory
    if _session_factory is None:
        _engine = _build_engine()
        _session_factory = sqlalchemy_async_sessionmaker(
            bind=_engine, expire_on_commit=False, autoflush=False
        )
    return _session_factory()


async def dispose() -> None:
    """Releases the pool on worker shutdown so connections are handed back
    promptly rather than waiting to time out server-side."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
