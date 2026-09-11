import pytest

from src.services.llm import (
    MIN_ANSWER_WORD_COUNT,
    _is_near_empty_answer,
    analyze_communication_with_llm,
    analyze_domain_with_llm,
)


def test_is_near_empty_answer_detects_blank_and_short_transcriptions():
    assert _is_near_empty_answer("") is True
    assert _is_near_empty_answer(None) is True
    assert _is_near_empty_answer("   ") is True
    assert _is_near_empty_answer("um") is True
    assert _is_near_empty_answer("I don't") is True  # 2 words, below the floor
    # "I don't know" (3 words) is intentionally NOT caught by the deterministic
    # short-circuit — it's a recognizable refusal phrase left to the LLM,
    # which now has an explicit instruction to score refusals as 0.
    assert _is_near_empty_answer("I don't know") is False
    assert _is_near_empty_answer("I think it is used for state management") is False


@pytest.mark.asyncio
async def test_analyze_domain_short_circuits_on_near_empty_transcription():
    """Regression guard: a near-empty answer must be scored as zero
    deterministically, without ever reaching the LLM (and therefore never
    risking a plausible-looking non-zero score for content that was never
    actually said)."""
    analysis, error, latency_ms, model = await analyze_domain_with_llm(
        user_profile={}, question_text="What is a closure?", transcription="uh"
    )
    assert error is None
    assert analysis["overall_score"] == 0
    assert analysis["strengths"] == []
    assert "no substantial answer" in analysis["improvements"][0].lower()
    assert latency_ms == 0  # confirms no real LLM call was made


@pytest.mark.asyncio
async def test_analyze_communication_short_circuits_on_near_empty_transcription():
    analysis, error, latency_ms, model = await analyze_communication_with_llm(
        user_profile={}, question_text="What is a closure?", transcription=""
    )
    assert error is None
    assert analysis["overall_score"] == 0
    assert analysis["strengths"] == []
    assert latency_ms == 0


def test_min_answer_word_count_is_low_enough_to_allow_real_short_answers():
    # A genuinely short but real answer (e.g. a one-line definition) must not
    # get caught by the same net as an empty/near-empty transcription.
    assert MIN_ANSWER_WORD_COUNT <= 3
