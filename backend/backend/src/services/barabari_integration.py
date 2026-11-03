"""Callback to Barabari's resume-score endpoint.

Runs as an arq background job (see src/worker/tasks.py), never inline in the
request path, so it never blocks or fails the caller's request if Barabari's
side is down. Raises on failure so arq's built-in retry can act on it - the
caller enqueues the job and moves on; arq's own logging covers final,
after-retries failures."""

import logging

import httpx

from src.config.manager import settings

logger = logging.getLogger(__name__)

RESUME_SCORE_PATH = "/samvaad-saathi/v1/resume-score"


def batches_connection() -> tuple[str, str]:
    """(base URL, service key) for the batch list and batch-choice calls to Sampark.

    SAMPARK_BATCHES_* when set, otherwise the SAMPARK_SAATHI_* pair the resume-score callback uses.
    Each is chosen separately, so a key can be shared while the address differs."""
    base = (settings.SAMPARK_BATCHES_BASE_URL or settings.SAMPARK_SAATHI_BASE_URL or "").strip().rstrip("/")
    key = (settings.SAMPARK_BATCHES_API_KEY or settings.SAMPARK_SAATHI_API_KEY or "").strip()
    return base, key


async def submit_resume_score_to_barabari(
    student_id: str,
    resume_score: int,
    request_id: str,
    target_role: str | None = None,
) -> None:
    api_key = settings.SAMPARK_SAATHI_API_KEY
    base_url = settings.SAMPARK_SAATHI_BASE_URL

    if not api_key or not base_url:
        logger.warning(
            "RequestId: %s | SAMPARK_SAATHI_API_KEY/BASE_URL not configured, skipping resume-score callback",
            request_id,
        )
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{base_url}{RESUME_SCORE_PATH}",
                headers={
                    "SAMVAAD-SAATHI-API-KEY": api_key,
                    "requestId": request_id,
                },
                json={
                    "studentUniqueId": student_id,
                    "resumeScore": resume_score,
                    "targetRole": target_role,
                },
            )
            response.raise_for_status()
            logger.info(
                "RequestId: %s | Resume score submitted to Barabari for student: %s",
                request_id, student_id,
            )
    except Exception:
        logger.exception(
            "RequestId: %s | Failed to submit resume score to Barabari for student: %s (will retry via arq if attempts remain)",
            request_id, student_id,
        )
        raise


BATCH_CHOICE_PATH = "/samvaad-saathi/v1/batch-choice"


async def submit_batch_choice_to_sampark(email: str, batch_key: str, request_id: str) -> None:
    """Tell Sampark which batch a learner chose, so its own records can follow.

    Runs as an arq job, never inline, so a Sampark outage can never fail the learner's choice.
    A learner who is not in Sampark is an ordinary answer (200), not a failure, so it is not
    retried. Anything else (a network error, a non-2xx reply) raises so arq retries."""
    base_url, api_key = batches_connection()
    if not api_key or not base_url:
        logger.warning(
            "RequestId: %s | Sampark batches connection not configured (SAMPARK_BATCHES_* or SAMPARK_SAATHI_*), "
            "skipping batch-choice callback",
            request_id,
        )
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{base_url}{BATCH_CHOICE_PATH}",
            headers={"SAMVAAD-SAATHI-API-KEY": api_key, "requestId": request_id},
            json={"email": email, "batchKey": batch_key},
        )
    response.raise_for_status()
    outcome = (response.json() or {}).get("data") or {}
    logger.info(
        "RequestId: %s | Sampark batch choice for batch %s: recorded=%s reason=%s",
        request_id, batch_key, outcome.get("recorded"), outcome.get("reason"),
    )
