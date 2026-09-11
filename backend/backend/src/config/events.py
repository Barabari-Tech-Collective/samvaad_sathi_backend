import typing

import fastapi
import loguru

from src.repository.events import dispose_db_connection, initialize_db_connection
from src.repository.redis_client import async_redis


def execute_backend_server_event_handler(backend_app: fastapi.FastAPI) -> typing.Any:
    async def launch_backend_server_events() -> None:
        await initialize_db_connection(backend_app=backend_app)

        backend_app.state.redis = async_redis.client
        try:
            await async_redis.client.ping()
            loguru.logger.info("Redis Connection --- Successfully Established!")
        except Exception as exc:
            # Non-fatal: rate limiting and TTS caching fail open when Redis is
            # unreachable, so a missing/down Redis must never block app startup.
            loguru.logger.warning(f"Redis not reachable at startup - rate limiting/caching will fail open: {exc}")

    return launch_backend_server_events


def terminate_backend_server_event_handler(backend_app: fastapi.FastAPI) -> typing.Any:
    @loguru.logger.catch
    async def stop_backend_server_events() -> None:
        await dispose_db_connection(backend_app=backend_app)
        await async_redis.dispose()

    return stop_backend_server_events
