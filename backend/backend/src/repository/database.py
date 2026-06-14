import os
import ssl
import pydantic
from sqlalchemy.ext.asyncio import (
    async_sessionmaker as sqlalchemy_async_sessionmaker,
    AsyncEngine as SQLAlchemyAsyncEngine,
    AsyncSession as SQLAlchemyAsyncSession,
    create_async_engine as create_sqlalchemy_async_engine,
)
from sqlalchemy.pool import Pool as SQLAlchemyPool, QueuePool as SQLAlchemyQueuePool

from src.config.manager import settings


class AsyncDatabase:
    def __init__(self):
        # Create SSL context for asyncpg
        # ssl_context = ssl.create_default_context()
        # ssl_context.check_hostname = False
        # ssl_context.verify_mode = ssl.CERT_NONE
        
        self.async_engine: SQLAlchemyAsyncEngine = create_sqlalchemy_async_engine(
            url=self.set_async_db_uri,
            echo=settings.IS_DB_ECHO_LOG,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_POOL_OVERFLOW,
            # Database connection optimizations
            pool_timeout=settings.DB_TIMEOUT,
            pool_pre_ping=True,  # Validate connections before use
            pool_recycle=3600,   # Recycle connections every hour
            # SSL configuration for asyncpg (Aiven/Supabase)
            # connect_args={"ssl": ssl_context}
            # connect_args=connect_args,
        )
        # Use session factory instead of single session for better concurrency
        self.async_session_factory: sqlalchemy_async_sessionmaker[SQLAlchemyAsyncSession] = sqlalchemy_async_sessionmaker(
            bind=self.async_engine,
            expire_on_commit=settings.IS_DB_EXPIRE_ON_COMMIT,
            class_=SQLAlchemyAsyncSession,
        )
        self.pool: SQLAlchemyPool = self.async_engine.pool
        
    def get_session(self) -> SQLAlchemyAsyncSession:
        """
        Get a new database session.
        Each call returns a new session instance for better concurrency support.
        """
        return self.async_session_factory()
    
    @property
    def async_session(self) -> SQLAlchemyAsyncSession:
        """
        Backward compatibility property.
        Note: For new code, prefer using get_session() method.
        """
        return self.get_session()

    @property
    def set_async_db_uri(self) -> str:
        """
        Set the synchronous database driver into asynchronous version by utilizing AsyncPG:
            `postgresql://` => `postgresql+asyncpg://`
        """
        return f"postgresql+asyncpg://{settings.DB_POSTGRES_USERNAME}:{settings.DB_POSTGRES_PASSWORD}@{settings.DB_POSTGRES_HOST}:{settings.DB_POSTGRES_PORT}/{settings.DB_POSTGRES_NAME}"


async_db: AsyncDatabase = AsyncDatabase()
