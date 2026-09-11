from arq.connections import RedisSettings

from src.config.manager import settings

redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    database=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD or None,
)
