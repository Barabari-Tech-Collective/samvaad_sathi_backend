import logging

import fastapi

from src.api.dependencies.auth import get_current_user
from src.models.db.user import User
from src.repository.redis_client import async_redis

logger = logging.getLogger(__name__)


def rate_limiter(*, key_prefix: str, limit: int, window_seconds: int):
    """
    Fixed-window per-user rate limit backed by the self-hosted Redis instance.

    Guards endpoints that call metered third-party APIs (OpenAI/ElevenLabs)
    against runaway cost from a single user or a client-side retry bug - this
    is a cost control, not a security boundary.

    Fails open (allows the request, logs a warning) on any Redis error,
    including Redis simply being unreachable. A Redis hiccup must never take
    the app down or block real users.
    """

    async def _check(current_user: User = fastapi.Depends(get_current_user)) -> None:
        redis_key = f"ratelimit:{key_prefix}:{current_user.id}"
        try:
            count = await async_redis.client.incr(redis_key)
            if count == 1:
                await async_redis.client.expire(redis_key, window_seconds)
        except Exception as exc:
            logger.warning("Rate limiter Redis error, failing open for key=%s: %s", redis_key, exc)
            return

        if count > limit:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({limit} requests per {window_seconds}s). Please try again shortly.",
            )

    return _check
