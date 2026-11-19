"""A cap on how many staff analytics requests run at once in this process.

Dashboard aggregates take seconds on a real data set, and each request holds a
database connection for its whole life (sign-in lookup included). Without a cap,
100 staff opening the Overview took every connection in the pool, and students
signing in or polling a transcript got 503s. Requests over the cap wait here,
before any connection is taken, and are refused with a retry hint if the wait
grows too long. Student traffic never waits on this.
"""

import asyncio
import json

from src.config.manager import settings


_compute: asyncio.Semaphore | None = None


class _Busy(Exception):
    pass


class compute_slot:
    """A slot for computing a cached dashboard aggregate on a cache miss.

    Cached dashboard views skip the request-level cap (a cache hit takes
    milliseconds and must not queue behind a slow computation); only the
    computation itself takes one of these slots.
    """

    async def __aenter__(self):
        global _compute
        if _compute is None:
            _compute = asyncio.Semaphore(settings.ANALYTICS_MAX_CONCURRENCY)
        try:
            await asyncio.wait_for(_compute.acquire(), timeout=settings.ANALYTICS_QUEUE_WAIT_SECONDS)
        except asyncio.TimeoutError:
            import fastapi

            raise fastapi.HTTPException(
                503,
                "The dashboard is busy. Try again in a few seconds.",
                headers={"Retry-After": "5"},
            )
        return self

    async def __aexit__(self, *exc):
        _compute.release()
        return False


class AnalyticsBulkhead:
    def __init__(
        self,
        app,
        *,
        prefixes: tuple[str, ...],
        limit: int,
        wait_seconds: float,
        exempt: tuple[str, ...] = (),
    ):
        self.app = app
        self.prefixes = prefixes
        self.exempt = exempt
        self.limit = limit
        self.wait_seconds = wait_seconds
        self._semaphore: asyncio.Semaphore | None = None

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not path.startswith(self.prefixes)
            or path.startswith(self.exempt)
        ):
            await self.app(scope, receive, send)
            return
        if self._semaphore is None:  # created inside the running event loop
            self._semaphore = asyncio.Semaphore(self.limit)
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.wait_seconds)
        except asyncio.TimeoutError:
            await _busy(send, self.wait_seconds)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self._semaphore.release()


async def _busy(send, wait_seconds: float) -> None:
    body = json.dumps({"detail": "The dashboard is busy. Try again in a few seconds."}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(max(1, int(wait_seconds // 4))).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def analytics_prefixes() -> tuple[str, ...]:
    api = settings.API_PREFIX.rstrip("/")
    return (f"{api}/v2/analytics/",)


def cached_dashboard_prefixes() -> tuple[str, ...]:
    """The Overview's views: cached, or light enough to need no cap."""
    api = settings.API_PREFIX.rstrip("/")
    return (f"{api}/v2/analytics/dashboard/",)
