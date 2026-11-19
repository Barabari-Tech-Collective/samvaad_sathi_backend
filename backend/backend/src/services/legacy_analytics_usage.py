"""ADR-3: measure the legacy analytics routes before removing them.

Every call to a /v2/analytics route is counted per route and per day in Redis, and the
response says it is deprecated. GET /v2/analytics-usage shows the last 30 days, so a
route is removed only after a full release with no calls. Counting never fails a request.
"""
from __future__ import annotations

import datetime as dt
import logging

import fastapi

from src.repository.redis_client import async_redis

_log = logging.getLogger(__name__)
KEY = "legacy-analytics:{day}"
KEEP_SECONDS = 40 * 24 * 3600


def _route_name(request: fastapi.Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


async def count_legacy_call(request: fastapi.Request, response: fastapi.Response) -> None:
    response.headers["Deprecation"] = "true"
    key = KEY.format(day=dt.date.today().isoformat())
    try:
        await async_redis.client.hincrby(key, _route_name(request), 1)
        await async_redis.client.expire(key, KEEP_SECONDS)
    except Exception:  # Redis down: the report still loads.
        _log.debug("legacy analytics usage not counted", exc_info=True)


async def usage(days: int = 30) -> dict[str, dict[str, int]]:
    """Calls per route per day, newest day first."""
    out: dict[str, dict[str, int]] = {}
    today = dt.date.today()
    for back in range(days):
        day = (today - dt.timedelta(days=back)).isoformat()
        try:
            rows = await async_redis.client.hgetall(KEY.format(day=day))
        except Exception:
            rows = {}
        if rows:
            out[day] = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in rows.items()}
    return out
