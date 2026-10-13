"""A curated interview must only ever serve the facilitator's published questions.

A facilitator writes questions for one role at one level and publishes them. A
student sitting that role is being assessed against that set, and the score only
means something if the questions actually came from there.

The platform broke that at every layer at once. The difficulty picker was a
hardcoded list of all four levels defaulting to medium, interview creation
checked only that the job profile existed, and when the chosen level turned out
to have no published questions the generator fell through to
`generate_full_stack_questions_with_llm` - so a student was assessed against a
facilitator's role on questions the facilitator had never seen, with nothing in
the response or the report saying so. A draft profile was equally sittable,
because the student path never looked at status. The v1 generator had no
job-profile awareness at all.

`test_curated_interviews_never_reach_a_generator` is the structural guard, and
the important test here: it is what stops a future edit quietly restoring the
fallback. The rest pin the boundaries of the rule.
"""

import ast
import pathlib
import types

import pytest

from src.models.db.job_profile import JobProfileStatus
from src.services.interview_eligibility import (
    DIFFICULTIES,
    MINIMUM_QUESTIONS_PER_LEVEL,
    available_difficulties,
    is_profile_interviewable,
    is_valid_difficulty,
    level_for_difficulty,
    select_published_questions,
    unavailable_reason,
)

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "api" / "routes"


def _profile(status=JobProfileStatus.PUBLISHED, easy=0, medium=0, hard=0, expert=0):
    """A stand-in for a JobProfile row or a JobProfileResponse.

    The eligibility helpers are duck-typed over `.status` plus the four count
    properties precisely so the ORM row and the API schema can both be passed.
    """
    return types.SimpleNamespace(
        status=status,
        easy_questions=easy,
        medium_questions=medium,
        hard_questions=hard,
        expert_questions=expert,
    )


def _question(question_id: int, level: int):
    return types.SimpleNamespace(id=question_id, level=level, question_text=f"Q{question_id}")


# --------------------------------------------------------------------------
# The reported scenario
# --------------------------------------------------------------------------


def test_level_with_no_published_questions_is_not_offered():
    """Facilitator published easy only; medium must not be startable."""
    profile = _profile(easy=MINIMUM_QUESTIONS_PER_LEVEL)

    assert available_difficulties(profile) == ["easy"]
    assert unavailable_reason(profile, "easy") is None
    assert unavailable_reason(profile, "medium") is not None


def test_refusal_tells_the_student_what_is_available():
    """The student picked a level that is not ready; say which one is."""
    profile = _profile(easy=MINIMUM_QUESTIONS_PER_LEVEL)

    reason = unavailable_reason(profile, "hard")

    assert "hard" in reason
    assert "easy" in reason, f"Refusal should name the available level, got: {reason!r}"


def test_selection_returns_nothing_rather_than_a_short_set():
    """Below the minimum the answer is no, not a shorter interview.

    An assessment whose length varies by student cannot be compared across
    students, and callers treat an empty list as a refusal - so this is also
    what stops a sparse level being topped up with generated questions.
    """
    sparse = [_question(i, level=1) for i in range(MINIMUM_QUESTIONS_PER_LEVEL - 1)]

    assert select_published_questions(sparse, difficulty="easy", count=5) == []


def test_selection_at_exactly_the_minimum_is_allowed():
    questions = [_question(i, level=1) for i in range(MINIMUM_QUESTIONS_PER_LEVEL)]

    selected = select_published_questions(questions, difficulty="easy", count=5)

    assert len(selected) == MINIMUM_QUESTIONS_PER_LEVEL


def test_selection_only_ever_returns_the_requested_level():
    """Mixing levels would mislabel the difficulty of a scored interview."""
    questions = [_question(i, level=1) for i in range(5)] + [
        _question(100 + i, level=2) for i in range(5)
    ]

    selected = select_published_questions(questions, difficulty="medium", count=5)

    assert {q.level for q in selected} == {2}
    assert all(q.id >= 100 for q in selected)


def test_retakes_get_a_different_slice():
    """A student resitting a level should not see the same opening five."""
    questions = [_question(i, level=1) for i in range(10)]

    first = select_published_questions(questions, difficulty="easy", count=5, offset=0)
    second = select_published_questions(questions, difficulty="easy", count=5, offset=5)

    assert [q.id for q in first] != [q.id for q in second]


def test_offset_wraps_without_dropping_questions():
    """A student past the end of the bank still gets a full sitting."""
    questions = [_question(i, level=1) for i in range(7)]

    selected = select_published_questions(questions, difficulty="easy", count=5, offset=35)

    assert len(selected) == 5


# --------------------------------------------------------------------------
# Publication state
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        JobProfileStatus.DRAFT,
        JobProfileStatus.UNDER_REVIEW,
        JobProfileStatus.CHANGES_REQUESTED,
    ],
)
def test_unpublished_profiles_are_never_interviewable(status):
    """Including changes_requested, where a reviewer asked for edits."""
    profile = _profile(status=status, easy=50, medium=50, hard=50, expert=50)

    assert is_profile_interviewable(profile) is False
    assert available_difficulties(profile) == []
    assert unavailable_reason(profile, "easy") is not None


def test_published_profile_with_enough_questions_is_interviewable():
    profile = _profile(easy=MINIMUM_QUESTIONS_PER_LEVEL)

    assert is_profile_interviewable(profile) is True


def test_published_but_empty_profile_explains_the_facilitator_must_act():
    profile = _profile()

    reason = unavailable_reason(profile, "easy")

    assert reason is not None
    assert "facilitator" in reason.lower()


# --------------------------------------------------------------------------
# Difficulty handling
# --------------------------------------------------------------------------


def test_unknown_difficulty_is_rejected_not_coerced():
    """Creation used to coerce an unrecognised difficulty to medium.

    On a curated profile that silently started a different facilitator's level
    than the student asked for.
    """
    assert is_valid_difficulty("medium") is True
    assert is_valid_difficulty("Medium") is False, "Callers must normalise case first"
    assert is_valid_difficulty("intermediate") is False
    assert is_valid_difficulty(None) is False

    with pytest.raises(ValueError):
        level_for_difficulty("intermediate")


def test_difficulty_level_mapping_is_stable():
    """These integers are stored on every published question row."""
    assert [level_for_difficulty(d) for d in DIFFICULTIES] == [1, 2, 3, 4]


def test_available_difficulties_are_ordered_easiest_first():
    profile = _profile(
        easy=MINIMUM_QUESTIONS_PER_LEVEL,
        hard=MINIMUM_QUESTIONS_PER_LEVEL,
        expert=MINIMUM_QUESTIONS_PER_LEVEL,
    )

    assert available_difficulties(profile) == ["easy", "hard", "expert"]


# --------------------------------------------------------------------------
# The structural guard
# --------------------------------------------------------------------------

_GENERATORS = {
    "generate_full_stack_questions_with_llm",
    "generate_interview_questions_with_llm",
}

#: Generation call sites that cannot be serving an assessment, with the reason.
#: Keyed by function name so a rename forces a fresh look rather than silently
#: widening the exemption. Each entry needs a test below proving the claim.
_PRACTICE_ONLY = {
    # Creates its own interview and never passes job_profile_id, so the
    # interview it generates for cannot be attached to a facilitator's role.
    # Proven by test_structure_practice_never_attaches_a_job_profile.
    "create_structure_practice_session",
}


def _functions_calling_generators(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in _GENERATORS:
                    names.add(node.name)
    return names


def _guards_curated_interviews(source: str, function_name: str) -> bool:
    """Does this function have any notion of a curated interview at all?

    Deliberately weak, and worth being clear about what it does and does not
    buy. A function that never mentions `job_profile_id` cannot possibly be
    separating assessments from practice, so this catches a *newly added*
    generation site written without that distinction - which is how the next
    instance of this bug would most likely arrive.

    It does not prove an existing guard still works: removing the branch while
    leaving other `job_profile_id` mentions in the function still passes here
    (verified by deliberately mutating it). The real proof that a curated
    interview is refused rather than generated for is behavioural, in
    tests/end_to_end_tests/test_curated_interview_integrity.py.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return "job_profile_id" in ast.dump(node)
    return False


def test_curated_interviews_never_reach_a_generator():
    """Every LLM generation path must first establish it is not an assessment.

    This is the guard that matters. The original defect was a bare
    `if not questions:` falling through to the LLM, which read as harmless
    cleanup. If a future edit reintroduces a generation call in a function that
    does not distinguish curated interviews, this fails.
    """
    unguarded = []
    checked = 0
    for path in sorted(ROUTES_DIR.glob("*.py")):
        source = path.read_text()
        for name in _functions_calling_generators(source):
            checked += 1
            if name not in _PRACTICE_ONLY and not _guards_curated_interviews(source, name):
                unguarded.append(f"{path.name}::{name}")

    assert checked, (
        "Found no question-generation call sites. The detection in "
        "_functions_calling_generators has gone stale - verify it before trusting a pass."
    )
    assert not unguarded, (
        "These functions generate questions without distinguishing a curated "
        "(facilitator job profile) interview from free practice:\n  "
        + "\n  ".join(unguarded)
        + "\nA curated interview must serve the facilitator's published questions or "
        "be refused - never an AI substitute. If the function genuinely cannot serve "
        "an assessment, add it to _PRACTICE_ONLY with a test proving that."
    )


def test_structure_practice_never_attaches_a_job_profile():
    """Proves the _PRACTICE_ONLY exemption for create_structure_practice_session.

    It is exempt from the generation guard only because the interview it creates
    carries no job profile and so can never be an assessment. If that changes,
    the exemption is wrong and the function needs the same guard as the others.
    """
    source = (ROUTES_DIR / "interviews_v2.py").read_text()

    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "create_structure_practice_session"
        ):
            assert "job_profile_id" not in ast.dump(node), (
                "create_structure_practice_session now references job_profile_id, so it "
                "may create an assessment. Remove it from _PRACTICE_ONLY and guard it."
            )
            return

    pytest.fail("create_structure_practice_session not found - update _PRACTICE_ONLY")
