"""arq task functions - run in the separate `arq` worker process, not inline
in a request. Add new background-safe operations here as this grows (e.g.
non-real-time report regeneration); real-time transcription (Whisper) stays
synchronous since the interview flow can't proceed without it.
"""

from arq.worker import Retry

from src.services.barabari_integration import submit_resume_score_to_barabari


async def submit_resume_score_task(
    ctx: dict,
    *,
    student_id: str,
    resume_score: int,
    request_id: str,
    target_role: str | None = None,
) -> None:
    """Wraps submit_resume_score_to_barabari for arq. Converts a plain
    exception into arq.worker.Retry with an escalating delay (2s, 4s, ...) so
    a briefly-down Barabari endpoint isn't hammered again within the same
    second - arq retries immediately otherwise. WorkerSettings.max_tries
    caps the total attempts."""
    try:
        await submit_resume_score_to_barabari(
            student_id=student_id,
            resume_score=resume_score,
            request_id=request_id,
            target_role=target_role,
        )
    except Exception as exc:
        job_try = ctx.get("job_try", 1)
        raise Retry(defer=2 * job_try) from exc
