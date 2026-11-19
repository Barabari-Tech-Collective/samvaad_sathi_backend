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


class PermanentCallbackError(Exception):
    """Sampark rejected the request in a way retrying cannot fix (for example 400 or 404)."""


def verify_sampark_integration_configuration() -> None:
    """Fail at startup, naming what is missing, when the Sampark callbacks cannot work.

    Without this, a missing setting made every resume-score callback return early with a log
    line, so scores never reached Sampark and nobody noticed (platform audit F18). Local work
    and tests opt out with SAMPARK_INTEGRATION_ENABLED=False.
    """
    if not getattr(settings, "SAMPARK_INTEGRATION_ENABLED", True):
        logger.warning("Sampark integration is off (SAMPARK_INTEGRATION_ENABLED=False): resume scores will not be sent")
        return
    missing = [
        name
        for name in ("SAMPARK_SAATHI_BASE_URL", "SAMPARK_SAATHI_API_KEY")
        if not str(getattr(settings, name, "") or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Sampark integration is on but not configured: set "
            + ", ".join(missing)
            + ". Without them no resume score reaches Sampark. For local work only, set "
            "SAMPARK_INTEGRATION_ENABLED=False."
        )


def is_permanent_status(status_code: int) -> bool:
    """4xx means the request itself is wrong; retrying will not help. 408 and 429 are the exceptions."""
    return 400 <= status_code < 500 and status_code not in (408, 429)


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
    backfill: bool = False,
) -> None:
    """Posts a resume score to Sampark. backfill=True asks Sampark to store it without messaging."""
    api_key = settings.SAMPARK_SAATHI_API_KEY
    base_url = settings.SAMPARK_SAATHI_BASE_URL

    if not api_key or not base_url:
        if not getattr(settings, "SAMPARK_INTEGRATION_ENABLED", True):
            return
        # Startup refuses this configuration; reaching here means settings changed at runtime.
        # Raise so the job is retried and shows up as failed, instead of being skipped silently.
        raise RuntimeError("SAMPARK_SAATHI_API_KEY/BASE_URL not configured")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{base_url}{RESUME_SCORE_PATH}",
                headers={
                    "SAMVAAD-SAATHI-API-KEY": api_key,
                    "requestId": request_id,
                    **({"X-Backfill": "true"} if backfill else {}),
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
    except httpx.HTTPStatusError as exc:
        if is_permanent_status(exc.response.status_code):
            raise PermanentCallbackError(
                f"Sampark refused the resume score for student {student_id}: HTTP {exc.response.status_code}"
            ) from exc
        logger.warning(
            "RequestId: %s | Resume score for student %s not accepted yet (HTTP %s); will retry",
            request_id, student_id, exc.response.status_code,
        )
        raise
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
        if not getattr(settings, "SAMPARK_INTEGRATION_ENABLED", True):
            return
        raise RuntimeError("Sampark batches connection not configured (SAMPARK_BATCHES_* or SAMPARK_SAATHI_*)")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{base_url}{BATCH_CHOICE_PATH}",
            headers={"SAMVAAD-SAATHI-API-KEY": api_key, "requestId": request_id},
            json={"email": email, "batchKey": batch_key},
        )
    if is_permanent_status(response.status_code):
        raise PermanentCallbackError(f"Sampark refused the batch choice: HTTP {response.status_code}")
    response.raise_for_status()
    outcome = (response.json() or {}).get("data") or {}
    logger.info(
        "RequestId: %s | Sampark batch choice for batch %s: recorded=%s reason=%s",
        request_id, batch_key, outcome.get("recorded"), outcome.get("reason"),
    )
