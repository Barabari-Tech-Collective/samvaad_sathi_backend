"""Core question-generation logic shared by the HTTP route and the arq background task."""

import asyncio
import logging

from src.services.llm import generate_interview_questions_with_llm
from src.services.syllabus_service import syllabus_service

logger = logging.getLogger(__name__)

LEVEL_MAP = {1: "easy", 2: "medium", 3: "hard", 4: "expert"}
BATCH_SIZE = 40
MAX_CONCURRENT_BATCHES = 5


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
        async with sem:
            logger.info(
                "Generating batch %d/%d (%d questions) for level %d (%s)",
                batch_idx + 1, len(batches), b_count, level, difficulty,
            )
            _, error, _, _, structured_items = await generate_interview_questions_with_llm(
                track=track,
                context_text=context_text,
                count=b_count,
                difficulty=difficulty,
                syllabus_topics=topics,
                ratio=ratio,
                influence=dict(influence),
            )
            if error or not structured_items:
                raise RuntimeError(
                    f"LLM batch {batch_idx + 1} failed for level {level} ({difficulty}): "
                    f"{error or 'no items returned'}"
                )
            return structured_items

    batch_results = await asyncio.gather(*[fetch_batch(b, i) for i, b in enumerate(batches)])

    items: list[tuple[int, str, dict]] = []
    for res in batch_results:
        for item in res:
            items.append((level, difficulty, item))
    return items
