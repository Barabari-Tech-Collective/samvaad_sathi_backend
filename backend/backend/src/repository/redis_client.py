import redis.asyncio as redis

from src.config.manager import settings


class AsyncRedis:
    def __init__(self):
        # redis.asyncio.Redis doesn't connect eagerly - constructing this never
        # raises even if Redis isn't reachable yet. Connection errors only
        # surface on the first actual command, which callers must handle
        # (see api/dependencies/rate_limit.py's fail-open pattern).
        self.client: redis.Redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    async def dispose(self) -> None:
        await self.client.aclose()


async_redis: AsyncRedis = AsyncRedis()
