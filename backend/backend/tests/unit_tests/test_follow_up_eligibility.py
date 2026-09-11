"""Regression guard: follow-up eligibility must be capped at the first 2
questions on every track, not just the non-tech flow.

Before this fix, generate_questions_v2 (tech / Full Stack Developer track)
tagged every base question with follow_up_strategy - either from the LLM's
own per-item "followUpStrategy" field, or the FOLLOW_UP_STRATEGY default
when the LLM returned plain strings. generate_full_stack_questions_with_llm's
prompt even instructs the model to set "default" on every question. Only
generate_non_tech_questions_v2 capped this to the first 2. Product rule is
that follow-ups should only ever be asked after question 1 or 2, on any
track - QA caught this because question 3 of a tech interview generated a
follow-up.

_apply_follow_up_eligibility is now the single place both routes call, so
the cap can't drift between tracks again.
"""

from src.api.routes.interviews_v2 import (
    FOLLOW_UP_STRATEGY,
    MAX_FOLLOW_UP_ELIGIBLE_QUESTIONS,
    _apply_follow_up_eligibility,
)


def _questions(count: int, **shared) -> list[dict[str, object]]:
    return [{"text": f"Question {i}", **shared} for i in range(count)]


def test_only_first_two_questions_are_eligible():
    questions_data = _questions(5)
    _apply_follow_up_eligibility(questions_data)

    eligible = [q["follow_up_strategy"] for q in questions_data]
    assert eligible == [FOLLOW_UP_STRATEGY, FOLLOW_UP_STRATEGY, None, None, None]


def test_overrides_an_llm_supplied_strategy_on_question_three_onward():
    """Regression case for the actual bug: the tech-track LLM prompt tags
    every question 'default', so a per-item value must not survive past the
    cap - this asserts the override, not just the default-unset case."""
    questions_data = _questions(3, follow_up_strategy="default")
    _apply_follow_up_eligibility(questions_data)

    assert questions_data[0]["follow_up_strategy"] == FOLLOW_UP_STRATEGY
    assert questions_data[1]["follow_up_strategy"] == FOLLOW_UP_STRATEGY
    assert questions_data[2]["follow_up_strategy"] is None


def test_fewer_questions_than_the_cap_does_not_raise():
    questions_data = _questions(1)
    _apply_follow_up_eligibility(questions_data)
    assert questions_data[0]["follow_up_strategy"] == FOLLOW_UP_STRATEGY


def test_empty_question_list_is_a_no_op():
    questions_data: list[dict[str, object]] = []
    _apply_follow_up_eligibility(questions_data)
    assert questions_data == []


def test_cap_constant_matches_the_stated_product_rule():
    """Pins the cap at 2 - if this ever needs to change, it should be a
    deliberate edit to the constant, not a silent behavior drift."""
    assert MAX_FOLLOW_UP_ELIGIBLE_QUESTIONS == 2
