import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.routes import job_profiles_v2
from src.api.routes.job_profiles_v2 import get_generate_questions_status
from src.services import question_generation
from src.services.question_generation_failures import safe_generation_failure_message


def _context(**_kwargs):
    return {"topics": {}, "ratio": {}, "influence": {}}


@pytest.mark.asyncio
async def test_200_question_bank_caps_parallel_level_generation(monkeypatch):
    active = 0
    peak = 0
    calls = 0

    async def provider(*, count, difficulty, **_kwargs):
        nonlocal active, peak, calls
        active += 1
        peak = max(peak, active)
        calls += 1
        await asyncio.sleep(0)
        active -= 1
        return (
            [],
            None,
            0,
            "controlled-test",
            [{"text": f"{difficulty}-{calls}-{index}"} for index in range(count)],
        )

    monkeypatch.setattr(
        question_generation.syllabus_service, "resolve_generation_context", _context
    )
    monkeypatch.setattr(
        question_generation, "generate_interview_questions_with_llm", provider
    )

    results = await question_generation.generate_question_levels(
        levels=[{"level": level, "count": 50} for level in range(1, 5)],
        track="Backend Developer",
        context_text="Test role",
        skills_list=["Python"],
        experience_level="fresher",
    )

    assert sum(len(level) for level in results) == 200
    assert calls == 12
    assert peak == question_generation.MAX_PARALLEL_LEVEL_GENERATIONS == 2


@pytest.mark.asyncio
async def test_transient_provider_error_is_retried_with_a_bounded_attempt_count(monkeypatch):
    calls = 0

    async def provider(*, count, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [], "429 rate limit", 0, "controlled-test", []
        return [], None, 0, "controlled-test", [{"text": str(index)} for index in range(count)]

    monkeypatch.setattr(
        question_generation.syllabus_service, "resolve_generation_context", _context
    )
    monkeypatch.setattr(
        question_generation, "generate_interview_questions_with_llm", provider
    )
    monkeypatch.setattr(question_generation, "LLM_RETRY_BASE_SECONDS", 0)

    result = await question_generation.generate_questions_for_level(
        level=1,
        count=20,
        track="Backend Developer",
        context_text="Test role",
        skills_list=[],
        experience_level=None,
    )

    assert len(result) == 20
    assert calls == 2


@pytest.mark.asyncio
async def test_failed_level_cancels_other_active_levels(monkeypatch):
    level_two_started = asyncio.Event()
    level_two_cancelled = asyncio.Event()

    async def generate_level(*, level, **_kwargs):
        if level == 2:
            level_two_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                level_two_cancelled.set()
                raise
        if level == 3:
            await level_two_started.wait()
            raise RuntimeError("permanent provider failure")
        return [(level, "easy", {"text": f"question-{level}"})]

    monkeypatch.setattr(question_generation, "generate_questions_for_level", generate_level)

    with pytest.raises(RuntimeError, match="permanent provider failure"):
        await question_generation.generate_question_levels(
            levels=[{"level": level, "count": 1} for level in range(1, 5)],
            track="Backend Developer",
            context_text=None,
            skills_list=[],
            experience_level=None,
        )

    assert level_two_cancelled.is_set()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("429 rate limit", "provider is busy"),
        ("gateway timed out", "took too long"),
        ("OPENAI_API_KEY is not configured", "temporarily unavailable"),
        ("database password=secret", "could not be completed"),
    ],
)
def test_failure_messages_are_actionable_without_leaking_internal_details(error, expected):
    message = safe_generation_failure_message(error)
    assert expected in message
    assert "secret" not in message


@pytest.mark.asyncio
async def test_status_returns_the_persisted_safe_failure_reason():
    repository = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(
                generation_token="job-token",
                generation_state="failed",
                generation_error="The AI provider is busy. Your draft is safe; retry in a moment.",
            )
        )
    )

    result = await get_generate_questions_status(
        job_profile_id=7,
        job_id="job-token",
        current_user=object(),
        job_profile_repo=repository,
    )

    assert result.status == "failed"
    assert result.error == "The AI provider is busy. Your draft is safe; retry in a moment."


@pytest.mark.asyncio
async def test_first_failed_poll_is_sanitized_before_it_reaches_the_staff_ui(monkeypatch):
    repository = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(
                generation_token="job-token",
                generation_state="queued",
                generation_error=None,
            )
        ),
        finish_generation=AsyncMock(),
    )
    monkeypatch.setattr(
        job_profiles_v2,
        "get_job_status",
        AsyncMock(return_value={"status": "failed", "error": "database password=secret"}),
    )

    result = await get_generate_questions_status(
        job_profile_id=7,
        job_id="job-token",
        current_user=object(),
        job_profile_repo=repository,
    )

    assert result.status == "failed"
    assert result.error == "Question generation could not be completed. Your draft is safe; retry."
    assert "secret" not in result.error


def test_comma_separated_lists_from_the_model_are_accepted():
    """Staging 2026-11-12: DeepSeek sent concepts_covered as one string and every batch failed."""
    from src.services.llm import QuestionsResponseLLM

    parsed = QuestionsResponseLLM.model_validate(
        {
            "items": [
                {
                    "text": "How would you isolate a failing micro-frontend after a deployment?",
                    "keywords": "error boundary; rollback",
                    "concepts_covered": "Error boundaries, micro-frontend isolation, graceful degradation",
                    "expected_answer": ["Isolate the module.", "Roll back."],
                }
            ]
        }
    )
    item = parsed.items[0]
    assert item.concepts_covered == [
        "Error boundaries",
        "micro-frontend isolation",
        "graceful degradation",
    ]
    assert item.keywords == ["error boundary", "rollback"]
    assert item.expected_answer == "Isolate the module.\nRoll back."


def test_shape_errors_are_not_reported_as_an_outage():
    from src.services.question_generation_failures import safe_generation_failure_message

    error = RuntimeError(
        "LLM batch 3 failed for level 3 (hard): 15 validation errors for QuestionsResponseLLM "
        "Input should be a valid list [input_value='database connection pooling, timeout handling']"
    )
    assert "unexpected format" in safe_generation_failure_message(error)
    assert "unavailable" in safe_generation_failure_message(RuntimeError("503 Service Unavailable"))
