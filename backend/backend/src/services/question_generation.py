"""Core question-generation logic shared by the HTTP route and the arq background task."""

import asyncio
import logging

from src.services.llm import generate_interview_questions_with_llm
from src.services.syllabus_service import syllabus_service

logger = logging.getLogger(__name__)

LEVEL_MAP = {1: "easy", 2: "medium", 3: "hard", 4: "expert"}
BATCH_SIZE = 20
MAX_CONCURRENT_BATCHES = 5
# LLMs don't reliably return exactly N items — ask for N + this buffer, then trim.
# At BATCH_SIZE=20, each question ≈ 250 tokens → 20 qs ≈ 5000 tokens; cap is 8000.
# Buffer of 4 uses ~1000 extra tokens, well within the cap.
BATCH_OVERAGE = 4


async def generate_questions_for_level(
    *,
    level: int,
    count: int,
    track: str,
    context_text: str | None,
    skills_list: list[str],
    experience_level: str | None,
    knowledge_reference_context: str | None = None,
) -> list[tuple[int, str, dict]]:
    """Generate all questions for one difficulty level.

    Returns a list of (level, difficulty, item_dict) tuples ready to be
    assembled into DB rows by the caller.

    Raises RuntimeError on LLM failure so both the route and the arq task
    get a plain exception (not an HTTPException) they can handle uniformly.
    """
    if count == 0:
        return []

    difficulty = LEVEL_MAP[level]

    role = syllabus_service._role_manager.derive_role(track)
    topic_bank = syllabus_service.get_topics_for_role(role=role, difficulty=difficulty)

    topics = {
        "tech": topic_bank.tech,
        "tech_allied": topic_bank.tech_allied,
        "behavioral": topic_bank.behavioral,
        "archetypes": topic_bank.archetypes,
        "depth_guidelines": topic_bank.depth_guidelines,
    }
    topics["tech_allied"] = syllabus_service.extract_tech_allied_from_resume(
        resume_text=context_text,
        skills=skills_list,
        fallback_topics=topics.get("tech_allied", []),
    )

    question_ratio = syllabus_service.compute_question_ratio(
        years_experience=None,
        has_resume_text=bool(context_text),
        has_skills=bool(skills_list),
    )
    ratio = {
        "tech": question_ratio.tech,
        "tech_allied": question_ratio.tech_allied,
        "behavioral": question_ratio.behavioral,
    }
    influence: dict = {
        "target_role": role,
        "difficulty": difficulty,
        "skills": skills_list,
        "experience_level": experience_level,
    }
    if knowledge_reference_context:
        influence["knowledge_reference_context"] = knowledge_reference_context

    remaining = count
    batches: list[int] = []
    while remaining > 0:
        batches.append(min(remaining, BATCH_SIZE))
        remaining -= batches[-1]

    sem = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

    async def fetch_batch(b_count: int, batch_idx: int) -> list[dict]:
        """Fetch exactly b_count questions from the LLM.

        Primary strategy: ask for b_count + BATCH_OVERAGE so the LLM almost
        always returns ≥ b_count even if it under-generates slightly, then trim
        to exactly b_count. This avoids retries in the happy path.

        Fallback (rare): if the response is still short after the inflated ask,
        retry once for the exact shortfall. This handles extreme under-generation.
        """
        async with sem:
            ask_count = b_count + BATCH_OVERAGE
            logger.info(
                "Batch %d/%d — level %d (%s): asking for %d (need %d, buffer %d)",
                batch_idx + 1, len(batches), level, difficulty,
                ask_count, b_count, BATCH_OVERAGE,
            )
            _, error, _, _, items = await generate_interview_questions_with_llm(
                track=track,
                context_text=context_text,
                count=ask_count,
                difficulty=difficulty,
                syllabus_topics=topics,
                ratio=ratio,
                influence=dict(influence),
            )
            if error or not items:
                raise RuntimeError(
                    f"LLM batch {batch_idx + 1} failed for level {level} ({difficulty}): "
                    f"{error or 'no items returned'}"
                )

            # Trim excess (happy path — we got enough)
            if len(items) >= b_count:
                return items[:b_count]

            # Fallback: LLM still came up short despite the buffer — retry for the gap
            shortfall = b_count - len(items)
            logger.warning(
                "Batch %d/%d: got %d/%d (asked %d) — retrying for %d shortfall",
                batch_idx + 1, len(batches), len(items), b_count, ask_count, shortfall,
            )
            _, retry_error, _, _, extra = await generate_interview_questions_with_llm(
                track=track,
                context_text=context_text,
                count=shortfall + BATCH_OVERAGE,
                difficulty=difficulty,
                syllabus_topics=topics,
                ratio=ratio,
                influence=dict(influence),
            )
            if extra and not retry_error:
                items = items + extra

            final = items[:b_count]
            if len(final) < b_count:
                logger.warning(
                    "Batch %d/%d: delivered %d/%d after retry — proceeding with partial",
                    batch_idx + 1, len(batches), len(final), b_count,
                )
            return final

    batch_results = await asyncio.gather(*[fetch_batch(b, i) for i, b in enumerate(batches)])

    all_raw: list[dict] = []
    for res in batch_results:
        all_raw.extend(res)

    if len(all_raw) != count:
        logger.warning(
            "Level %d (%s): final count %d does not match requested %d",
            level, difficulty, len(all_raw), count,
        )

    return [(level, difficulty, item) for item in all_raw[:count]]
