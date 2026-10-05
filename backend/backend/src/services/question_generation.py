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
    category: str | None = None,
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

    # resolve_generation_context handles tech/non-tech routing correctly,
    # including zeroing tech topics and setting is_non_tech for HR/sales/marketing
    # roles. The previous hand-rolled block omitted category/is_non_tech from the
    # influence dict, so non-tech roles silently got a tech prompt.
    gen_ctx = syllabus_service.resolve_generation_context(
        track=track,
        category=category,
        difficulty=difficulty,
        context_text=context_text or "",
        skills_list=skills_list,
        experience_level=experience_level,
    )
    topics: dict = gen_ctx["topics"]
    ratio: dict = gen_ctx["ratio"]
    influence: dict = dict(gen_ctx["influence"])
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
