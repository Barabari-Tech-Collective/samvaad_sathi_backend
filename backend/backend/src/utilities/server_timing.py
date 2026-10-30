"""Per-request step timings, sent as a standard Server-Timing header.

Browsers show it per request (DevTools > Network > Timing, and
performance.getEntriesByType("resource")[i].serverTiming), so slow steps can be found
on a deployed environment without server access. Off unless SERVER_TIMING_ENABLED is
set, because timings describe internals that production should not publish.
"""

import contextlib
import contextvars
import time

_steps: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "server_timing_steps", default=None
)


@contextlib.contextmanager
def step(name: str):
    """Time a block. Repeated names add up. Does nothing when timing is off."""
    steps = _steps.get()
    if steps is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        steps[name] = steps.get(name, 0.0) + (time.perf_counter() - started) * 1000


def install(app) -> None:
    @app.middleware("http")
    async def server_timing(request, call_next):
        steps: dict[str, float] = {}
        _steps.set(steps)
        started = time.perf_counter()
        response = await call_next(request)
        total = (time.perf_counter() - started) * 1000
        parts = [f"{name};dur={ms:.1f}" for name, ms in steps.items()]
        parts.append(f"total;dur={total:.1f}")
        response.headers["Server-Timing"] = ", ".join(parts)
        return response
