"""Job enqueue helper for the FastAPI app side (as opposed to src/worker/main.py,
which is the worker process that consumes jobs). Kept separate from the
worker's own RedisSettings because arq's create_pool() pings Redis and
retries on failure by default (5 retries x 1s) - fine for a worker process
waiting for Redis at startup, but would make a request hang for ~5s if Redis
is briefly down. This pool fails fast instead so enqueueing can stay best-effort.
"""

import logging

from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.jobs import Job as ArqJob

from src.config.manager import settings

logger = logging.getLogger(__name__)

_enqueue_redis_settings = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    database=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD or None,
    conn_timeout=1,
    conn_retries=0,
)

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis | None:
    global _pool
    if _pool is not None:
        return _pool
    try:
        _pool = await create_pool(_enqueue_redis_settings)
        return _pool
    except Exception as exc:
        logger.warning("Could not connect to Redis for arq job queue: %s", exc)
        return None


async def enqueue_job(function_name: str, **kwargs) -> str | None:
    """Best-effort enqueue. Returns the job_id string if queued, None otherwise
    (Redis down, or the pool is already known-broken) - never raises, so a Redis
    outage can never fail the caller's request.

    Existing callers that do ``if not await enqueue_job(...)`` still work because
    None is falsy and a job_id string is truthy.
    """
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        job = await pool.enqueue_job(function_name, **kwargs)
        return job.job_id if job else None
    except Exception as exc:
        logger.warning("Failed to enqueue arq job %s: %s", function_name, exc)
        return None


async def get_job_status(job_id: str) -> dict:
    """Return the current status of an arq job as a plain dict.

    Possible ``status`` values mirror arq's JobStatus enum:
    ``queued``, ``in_progress``, ``complete``, ``deferred``, ``not_found``, ``failed``.
    """
    pool = await _get_pool()
    if pool is None:
        return {"status": "unknown", "error": "Redis unavailable"}
    try:
        job = ArqJob(job_id, pool)
        status = await job.status()
        result: dict = {"status": status.value}
        if status.value == "complete":
            info = await job.result_info()
            if info is not None:
                if info.success:
                    result["result"] = info.result
                else:
                    result["status"] = "failed"
                    result["error"] = str(info.result)
        return result
    except Exception as exc:
        logger.warning("Failed to get arq job status for %s: %s", job_id, exc)
        return {"status": "unknown", "error": str(exc)}
