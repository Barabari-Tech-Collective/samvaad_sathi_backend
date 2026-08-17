"""Callback to Barabari's resume-score endpoint. Best-effort - never blocks or
fails the caller's request if Barabari's side is down."""

import logging

import httpx

from src.config.manager import settings

logger = logging.getLogger(__name__)

RESUME_SCORE_PATH = "/samvaad-saathi/v1/resume-score"


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
            "RequestId: %s | Failed to submit resume score to Barabari for student: %s",
            request_id, student_id,
        )
