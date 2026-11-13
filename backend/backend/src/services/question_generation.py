"""Core question-generation logic shared by the HTTP route and the arq background task."""

import asyncio
import logging

from src.services.llm import generate_interview_questions_with_llm
from src.services.syllabus_service import syllabus_service

logger = logging.getLogger(__name__)

LEVEL_MAP = {1: "easy", 2: "medium", 3: "hard", 4: "expert"}
BATCH_SIZE = 20
# LLMs don't reliably return exactly N items — ask for N + this buffer, then trim.
# At BATCH_SIZE=20, each question ≈ 250 tokens → 20 qs ≈ 5000 tokens; cap is 8000.
# Buffer of 4 uses ~1000 extra tokens, well within the cap.
BATCH_OVERAGE = 4
MAX_LLM_BATCH_ATTEMPTS = 3
LLM_RETRY_BASE_SECONDS = 0.5
MAX_PARALLEL_LEVEL_GENERATIONS = 2


def is_transient_generation_error(error: object) -> bool:
    """Whether a provider error is safe to retry without changing the draft."""
    text = str(error or "").lower()
    return any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "timeout",
            "timed out",
            "temporar",
            "connection",
            "502",
            "503",
            "504",
            "service unavailable",
        )
    )


MAX_TOP_UP_ROUNDS = 3


async def generate_question_batch_with_retry(**kwargs):
    """Call the provider with bounded retry/backoff for transient failures."""
    last_result = None
    for attempt in range(MAX_LLM_BATCH_ATTEMPTS):
        result = await generate_interview_questions_with_llm(**kwargs)
        _, error, _, _, items = result
        if not error and items:
            return result
        last_result = result
        if not is_transient_generation_error(error) or attempt == MAX_LLM_BATCH_ATTEMPTS - 1:
            return result
        delay = LLM_RETRY_BASE_SECONDS * (2**attempt)
        logger.warning(
            "Question-generation provider attempt %d/%d failed transiently; retrying in %.1fs",
            attempt + 1,
            MAX_LLM_BATCH_ATTEMPTS,
            delay,
        )
        await asyncio.sleep(delay)
    return last_result


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
    existing_questions: list[str] | None = None,
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
    # Pass the integer level so llm.py can inject level-specific archetype constraints.
    influence["level"] = level
    if knowledge_reference_context:
        influence["knowledge_reference_context"] = knowledge_reference_context

    # Repeats are dropped as batches arrive, so the spare questions asked for in each batch
    # fill the gaps, and short levels are topped up. Saving 149 of 200 because the model
    # repeated itself is what this prevents.
    def norm(text: str) -> str:
        return " ".join(str(text or "").split()).casefold()

    seen = {norm(t) for t in (existing_questions or []) if norm(t)}
    all_raw: list[dict] = []

    def take(items: list[dict] | None, room: int) -> int:
        added = 0
        for item in items or []:
            key = norm(item.get("text"))
            if added >= room or not key or key in seen:
                continue
            seen.add(key)
            all_raw.append(item)
            added += 1
        return added

    async def ask(n: int) -> list[dict] | None:
        batch_influence = dict(influence)
        avoid = [q["text"] for q in all_raw] + list(existing_questions or [])
        if avoid:
            batch_influence["exclude_questions"] = avoid[-200:]
        _, error, _, _, items = await generate_question_batch_with_retry(
            track=track,
            context_text=context_text,
            count=n + BATCH_OVERAGE,
            difficulty=difficulty,
            syllabus_topics=topics,
            ratio=ratio,
            influence=batch_influence,
        )
        if error:
            logger.warning("Level %d (%s): batch error %s", level, difficulty, error)
            return None
        return items

    remaining = count
    batches: list[int] = []
    while remaining > 0:
        batches.append(min(remaining, BATCH_SIZE))
        remaining -= batches[-1]

    for batch_idx, b_count in enumerate(batches):
        logger.info(
            "Batch %d/%d — level %d (%s): need %d, have %d",
            batch_idx + 1, len(batches), level, difficulty, b_count, len(all_raw),
        )
        items = await ask(b_count)
        if items is None and not all_raw and batch_idx == 0:
            raise RuntimeError(
                f"LLM batch {batch_idx + 1} failed for level {level} ({difficulty}): no items returned"
            )
        take(items, b_count)

    for attempt in range(MAX_TOP_UP_ROUNDS):
        short = count - len(all_raw)
        if short <= 0:
            break
        logger.warning(
            "Level %d (%s): %d/%d after removing repeats — top-up %d/%d",
            level, difficulty, len(all_raw), count, attempt + 1, MAX_TOP_UP_ROUNDS,
        )
        take(await ask(min(short, BATCH_SIZE)), short)

    if not all_raw:
        raise RuntimeError(f"LLM returned no usable questions for level {level} ({difficulty})")
    if len(all_raw) != count:
        logger.warning(
            "Level %d (%s): final count %d does not match requested %d",
            level, difficulty, len(all_raw), count,
        )

    return [(level, difficulty, item) for item in all_raw[:count]]


async def generate_question_levels(
    *,
    levels: list[dict],
    track: str,
    context_text: str | None,
    skills_list: list[str],
    experience_level: str | None,
    category: str | None = None,
    knowledge_reference_context: str | None = None,
    existing_questions: dict[int, list[str]] | None = None,
) -> list[list[tuple[int, str, dict]]]:
    """Generate selected levels with a small global provider-concurrency cap.

    A 200-question bank has twelve LLM batches. Limiting level concurrency to
    two prevents one staff action from exhausting provider rate limits or a
    worker's outbound connection pool. The caller writes only after every
    level succeeds, keeping the bank atomic.
    """
    semaphore = asyncio.Semaphore(MAX_PARALLEL_LEVEL_GENERATIONS)

    async def generate_level(level_config: dict) -> list[tuple[int, str, dict]]:
        async with semaphore:
            return await generate_questions_for_level(
                level=level_config["level"],
                count=level_config["count"],
                track=track,
                context_text=context_text,
                skills_list=skills_list,
                experience_level=experience_level,
                category=category,
                knowledge_reference_context=knowledge_reference_context,
                existing_questions=(existing_questions or {}).get(level_config["level"]),
            )

    tasks = [asyncio.create_task(generate_level(level)) for level in levels]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
