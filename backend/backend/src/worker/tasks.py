"""arq task functions - run in the separate `arq` worker process, not inline
in a request. Add new background-safe operations here as this grows (e.g.
non-real-time report regeneration); real-time transcription (Whisper) stays
synchronous since the interview flow can't proceed without it.
"""

import asyncio
import logging

from arq.worker import Retry

from src.services.barabari_integration import submit_resume_score_to_barabari

logger = logging.getLogger(__name__)


async def generate_questions_task(
    ctx: dict,
    *,
    job_profile_id: int,
    levels: list[dict],
    knowledge_reference_context: str | None = None,
) -> dict:
    """Generate interview questions for a job profile and persist them to the DB.

    ``levels`` is a list of dicts with keys ``level`` (int) and ``count`` (int).
    Returns ``{"count": <number of questions saved>}`` on success.
    Raises on failure so arq marks the job as failed.
    """
    from src.repository.crud.job_profile import JobProfileCRUDRepository
    from src.repository.database import async_db
    from src.services.question_generation import generate_questions_for_level

    session = async_db.get_session()
    try:
        repo = JobProfileCRUDRepository(async_session=session)
        profile = await repo.get_by_id(job_profile_id=job_profile_id)
        if profile is None:
            raise ValueError(f"Job profile {job_profile_id} not found")

        track = profile.job_name
        context_text = profile.job_description
        skills_list = profile.skills or []

        level_tasks = [
            generate_questions_for_level(
                level=l["level"],
                count=l["count"],
                track=track,
                context_text=context_text,
                skills_list=skills_list,
                experience_level=profile.experience_level,
                category=profile.category,
                knowledge_reference_context=knowledge_reference_context,
            )
            for l in levels
        ]
        results = await asyncio.gather(*level_tasks)

        generated_questions_data = []
        for level_results in results:
            for level, difficulty, item in level_results:
                generated_questions_data.append({
                    "job_profile_id": profile.id,
                    "question_text": item["text"],
                    "level": level,
                    "difficulty": difficulty,
                    "question_type": item.get("category", "theoretical"),
                    "is_ai_generated": True,
                    "keywords": item.get("keywords") or [],
                    "concepts_covered": item.get("concepts_covered") or [],
                    "expected_answer": item.get("expected_answer"),
                    "example_output": item.get("example_output"),
                })

        db_questions = await repo.create_job_profile_questions(generated_questions_data)
        await session.commit()
        logger.info("generate_questions_task: saved %d questions for job profile %d", len(db_questions), job_profile_id)
        return {"count": len(db_questions)}
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


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
