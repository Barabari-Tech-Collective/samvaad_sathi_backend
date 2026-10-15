from arq.connections import RedisSettings

from src.config.manager import settings


def build_redis_settings(*, fail_fast: bool = False) -> RedisSettings:
    if settings.REDIS_URL:
        result = RedisSettings.from_dsn(settings.REDIS_URL)
    else:
        result = RedisSettings(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            database=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
        )
    if fail_fast:
        result.conn_timeout = 1
        result.conn_retries = 0
    return result


redis_settings = build_redis_settings()
