import logging
import time
from collections import defaultdict, deque
from threading import Lock

import fastapi

from src.api.dependencies.auth import get_current_user
from src.models.db.user import User
from src.repository.redis_client import async_redis

logger = logging.getLogger(__name__)


# In-process fallback used when Redis is unreachable. Redis remains the
# correct backend (it is shared across workers); this exists so that a
# missing/down Redis degrades protection rather than removing it entirely.
# Previously every limiter failed fully open, which meant an environment
# without Redis - staging, for example - had no rate limiting whatsoever.
_LOCAL_HITS: dict[str, deque] = defaultdict(deque)
_LOCAL_LOCK = Lock()
# Bound the number of tracked keys so an attacker cycling identifiers
# cannot grow this dict without limit.
_LOCAL_MAX_KEYS = 10_000


def _local_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    """Sliding-window counter. Returns True if the caller is over the limit."""
    now = time.monotonic()
    cutoff = now - window_seconds
    with _LOCAL_LOCK:
        if len(_LOCAL_HITS) > _LOCAL_MAX_KEYS:
            for stale_key in [
                k for k, v in _LOCAL_HITS.items() if not v or v[-1] < cutoff
            ]:
                _LOCAL_HITS.pop(stale_key, None)

        hits = _LOCAL_HITS[key]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False


def _too_many(limit: int, window_seconds: int) -> fastapi.HTTPException:
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded ({limit} requests per {window_seconds}s). Please try again shortly.",
        headers={"Retry-After": str(window_seconds)},
    )


async def _enforce(key: str, limit: int, window_seconds: int) -> None:
    """Count this hit against `key`, raising 429 when over the limit.

    Tries Redis first; on any Redis error falls back to the in-process
    counter rather than allowing the request unconditionally.
    """
    try:
        count = await async_redis.client.incr(key)
        if count == 1:
            await async_redis.client.expire(key, window_seconds)
    except Exception as exc:
        logger.warning(
            "Rate limiter Redis unavailable, using in-process fallback for key=%s: %s",
            key,
            exc,
        )
        if _local_rate_limited(key, limit, window_seconds):
            raise _too_many(limit, window_seconds)
        return

    if count > limit:
        raise _too_many(limit, window_seconds)


def rate_limiter(*, key_prefix: str, limit: int, window_seconds: int):
    """Per-user rate limit for authenticated endpoints that call metered APIs."""

    async def _check(current_user: User = fastapi.Depends(get_current_user)) -> None:
        await _enforce(f"ratelimit:{key_prefix}:{current_user.id}", limit, window_seconds)

    return _check


def anonymous_rate_limiter(*, key_prefix: str, limit: int, window_seconds: int):
    """Per-client-IP rate limit for endpoints that have no authenticated user yet.

    Used to put a ceiling on credential-stuffing and signup abuse against
    /login and /users, which previously had no throttling of any kind.
    """

    async def _check(request: fastapi.Request) -> None:
        client_ip = "unknown"
        # Render/nginx terminate TLS upstream, so the socket peer is the proxy.
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        await _enforce(f"ratelimit:{key_prefix}:{client_ip}", limit, window_seconds)

    return _check
