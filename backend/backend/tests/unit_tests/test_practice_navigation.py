import datetime
from types import SimpleNamespace

import pytest

from src.models.db.interview import Interview
from src.services.practice_navigation import (
    NavigationConflict,
    apply_navigation_transition,
    initialize_navigation,
    question_snapshot_hash,
)


def test_current_question_foreign_key_is_drop_safe_for_schema_rebuilds():
    foreign_key = next(iter(Interview.__table__.c.current_question_id.foreign_keys))

    assert foreign_key.name == "fk_interview_current_question_id_interview_question"
    assert foreign_key.use_alter is True


def question(
    question_id: int,
    order: int,
    *,
    follow_up: bool = False,
    parent_id: int | None = None,
    strategy: str | None = None,
):
    return SimpleNamespace(
        id=question_id,
        order=order,
        text=f"Question {question_id}",
        topic="topic",
        category="domain",
        expected_answer=None,
        follow_up_strategy=strategy,
        is_follow_up=follow_up,
        parent_question_id=parent_id,
        status="pending",
    )


def attempt(
    attempt_id: int,
    *,
    answer_version: int = 1,
    follow_up_disposition: str | None = "not_applicable",
):
    return SimpleNamespace(
        id=attempt_id,
        answer_saved_at=datetime.datetime.now(datetime.timezone.utc),
        audio_url="answer.webm",
        transcription=None,
        answer_version=answer_version,
        attempt_state="answer_saved",
        skipped_at=None,
        follow_up_disposition=follow_up_disposition,
        follow_up_resolved_at=None,
    )


def interview(*, current_question_id: int | None = 1, state_version: int = 1):
    return SimpleNamespace(
        current_question_id=current_question_id,
        state_version=state_version,
        question_set_version=1,
        question_set_hash=None,
        question_set_frozen_at=None,
        last_transition_id=None,
        last_transition_response=None,
    )


def test_double_tap_replays_one_transition_without_skipping_twice():
    questions = [question(1, 1), question(2, 2), question(3, 3)]
    saved = attempt(11)
    sitting = interview()

    first = apply_navigation_transition(
        interview=sitting,
        questions=questions,
        attempts_by_question_id={1: saved},
        from_question_id=1,
        expected_state_version=1,
        transition_id="transition-1",
        action="answer_saved",
        answer_version=1,
    )
    replay = apply_navigation_transition(
        interview=sitting,
        questions=questions,
        attempts_by_question_id={1: saved},
        from_question_id=1,
        expected_state_version=1,
        transition_id="transition-1",
        action="answer_saved",
        answer_version=1,
    )

    assert first.current_question_id == 2
    assert replay.current_question_id == 2
    assert replay.duplicate is True
    assert sitting.state_version == 2


def test_stale_tab_is_rejected_without_changing_question_state():
    questions = [question(1, 1), question(2, 2)]
    saved = attempt(11)
    sitting = interview(state_version=4)

    with pytest.raises(NavigationConflict) as error:
        apply_navigation_transition(
            interview=sitting,
            questions=questions,
            attempts_by_question_id={1: saved},
            from_question_id=1,
            expected_state_version=3,
            transition_id="transition-stale",
            action="answer_saved",
            answer_version=1,
        )

    assert error.value.code == "INTERVIEW_STATE_CONFLICT"
    assert sitting.current_question_id == 1
    assert questions[0].status == "pending"


def test_pending_follow_up_rejection_has_no_partial_mutation():
    questions = [
        question(1, 1, strategy="llm_transcription_based"),
        question(2, 2),
    ]
    saved = attempt(11, follow_up_disposition="pending")
    sitting = interview()

    with pytest.raises(NavigationConflict) as error:
        apply_navigation_transition(
            interview=sitting,
            questions=questions,
            attempts_by_question_id={1: saved},
            from_question_id=1,
            expected_state_version=1,
            transition_id="transition-pending",
            action="answer_saved",
            answer_version=1,
        )

    assert error.value.code == "FOLLOW_UP_PENDING"
    assert questions[0].status == "pending"
    assert saved.attempt_state == "answer_saved"
    assert sitting.current_question_id == 1


def test_continue_without_follow_up_persists_skip_and_moves_to_base_question():
    parent = question(1, 1, strategy="llm_transcription_based")
    child = question(3, 3, follow_up=True, parent_id=1)
    next_base = question(2, 2)
    saved = attempt(11, follow_up_disposition="pending")
    child_attempt = SimpleNamespace(
        attempt_state="in_progress",
        skipped_at=None,
        answer_saved_at=None,
        audio_url=None,
        transcription=None,
    )
    sitting = interview()

    result = apply_navigation_transition(
        interview=sitting,
        questions=[parent, child, next_base],
        attempts_by_question_id={1: saved, 3: child_attempt},
        from_question_id=1,
        expected_state_version=1,
        transition_id="transition-continue",
        action="continue_without_follow_up",
        answer_version=1,
    )

    assert result.current_question_id == 2
    assert saved.follow_up_disposition == "skipped"
    assert child.status == "skipped"
    assert child_attempt.attempt_state == "skipped"


def test_resume_keeps_explicit_checkpoint_even_when_answer_is_already_saved():
    questions = [question(1, 1), question(2, 2)]
    sitting = interview(current_question_id=1, state_version=7)

    changed = initialize_navigation(sitting, questions, {1: attempt(11)})

    assert changed is False
    assert sitting.current_question_id == 1
    assert sitting.state_version == 7


def test_snapshot_hash_excludes_adaptive_children_but_detects_base_edits():
    first = question(1, 1)
    second = question(2, 2)
    baseline = question_snapshot_hash([first, second])

    child = question(3, 3, follow_up=True, parent_id=1)
    assert question_snapshot_hash([first, child, second]) == baseline

    second.text = "Changed after interview start"
    assert question_snapshot_hash([first, second]) != baseline
