from src.config.manager import settings
from src.repository.redis_client import async_redis

_REVOKED_KEY_PREFIX = "SSO_ACCESS_REVOKED-"


def _key(unique_id: str) -> str:
    return f"{_REVOKED_KEY_PREFIX}{unique_id}"


async def is_access_revoked(unique_id: str) -> bool:
    """
    Checked by get_current_user after a successful SSO token decode. Fails open (treats
    Redis being unreachable the same as "not revoked") to match this app's existing
    Redis-down behavior elsewhere (rate limiting, caching) - a missing revocation signal
    degrades back to pre-Phase-6 behavior, it doesn't take the app down.
    """
    try:
        return bool(await async_redis.client.exists(_key(unique_id)))
    except Exception:
        return False


async def set_access_revoked(unique_id: str) -> None:
    await async_redis.client.set(_key(unique_id), "1", ex=settings.ACCESS_REVOKED_FLAG_TTL_SECONDS)


async def clear_access_revoked(unique_id: str) -> None:
    await async_redis.client.delete(_key(unique_id))
