import datetime
from types import SimpleNamespace as Item

import fastapi
import pydantic
import pytest

from src.models.schemas.interview import InterviewCreate
from src.models.schemas.job_profile import JobProfileCreateV2, JobProfileUpdateV2
from src.services.fixed_interview_lifecycle import begin, receipt, require_answer_window
from src.services.interview_question_plan import (
    make_curated_plan,
    validate_initial_questions,
)


@pytest.mark.parametrize("value", [0, -1, 51, True, False, 2.5, "8", None])
def test_creator_count_rejects_invalid_or_coerced_values(value):
    with pytest.raises(pydantic.ValidationError):
        JobProfileCreateV2(jobName="Role", questionsPerInterview=value)


@pytest.mark.parametrize("value", [1, 3, 8, 50])
def test_creator_count_accepts_supported_boundaries(value):
    assert (
        JobProfileCreateV2(
            jobName="Role", questionsPerInterview=value
        ).questions_per_interview
        == value
    )


def test_omitted_update_keeps_count_but_explicit_clear_is_rejected():
    assert "questions_per_interview" not in JobProfileUpdateV2(
        jobName="Updated"
    ).model_dump(exclude_unset=True)
    with pytest.raises(pydantic.ValidationError):
        JobProfileUpdateV2(questionsPerInterview=None)


def test_curated_student_cannot_override_creator_or_ask_for_unbounded_generation():
    with pytest.raises(pydantic.ValidationError):
        InterviewCreate(track="Role", jobProfileId=1, questionCount=3)
    with pytest.raises(pydantic.ValidationError):
        InterviewCreate(track="Free practice", questionCount=11)


def profile(total=3):
    return Item(
        id=7,
        questions_per_interview=total,
        questions=[
            Item(
                id=i + 1,
                level=1,
                question_text=f"Question {i}",
                expected_answer=f"Private {i}",
            )
            for i in range(8)
        ],
    )


def test_snapshot_rotation_is_unique_and_survives_source_changes():
    source = profile()
    plan = make_curated_plan(source, difficulty="easy", offset=7)
    assert [q["job_profile_question_id"] for q in plan["questions"]] == [8, 1, 2]
    source.questions[7].question_text = "Edited later"
    assert plan["questions"][0]["text"] == "Question 7"
    assert plan["adaptive_slots"] == 0


def test_duplicate_bank_cannot_fake_sufficient_coverage():
    source = profile()
    for question in source.questions:
        question.question_text = "Same  question"
    with pytest.raises(fastapi.HTTPException) as error:
        make_curated_plan(source, difficulty="easy")
    assert error.value.status_code == 409


def test_persistence_preserves_rubric_and_disables_dynamic_followups():
    plan = make_curated_plan(profile(), difficulty="easy")
    sitting = Item(question_plan=plan)
    data = [
        {"text": q["text"], "follow_up_strategy": "default"} for q in plan["questions"]
    ]
    stored = validate_initial_questions(sitting, data)
    assert stored[0]["expected_answer"] == "Private 0"
    assert all(q["follow_up_strategy"] is None for q in stored)
    with pytest.raises(fastapi.HTTPException):
        validate_initial_questions(sitting, data[:-1])


def sitting():
    return Item(
        id=8,
        status="active",
        interview_mode="assessment",
        started_at=None,
        answer_deadline_at=None,
        question_plan={
            "version": 1,
            "total": 3,
            "duration_seconds": 540,
            "adaptive_slots": 0,
        },
    )


def test_begin_is_idempotent_and_does_not_start_during_preparation():
    now = datetime.datetime.now(datetime.timezone.utc)
    value = sitting()
    with pytest.raises(fastapi.HTTPException):
        begin(value, [], now=now)
    assert value.started_at is None
    first = begin(value, [1, 2, 3], now=now)
    repeated = begin(value, [1, 2, 3], now=now + datetime.timedelta(seconds=30))
    assert first["answerDeadlineAt"] == repeated["answerDeadlineAt"]


def test_deadline_rejects_new_answers_but_allows_bounded_inflight_save():
    value = sitting()
    now = datetime.datetime.now(datetime.timezone.utc)
    begin(value, [1, 2, 3], now=now)
    after = value.answer_deadline_at + datetime.timedelta(seconds=20)
    with pytest.raises(fastapi.HTTPException):
        require_answer_window(value, now=after)
    require_answer_window(value, now=after, attempt=Item(created_at=now))
    with pytest.raises(fastapi.HTTPException):
        require_answer_window(
            value,
            now=value.answer_deadline_at + datetime.timedelta(seconds=61),
            attempt=Item(created_at=now),
        )


def test_receipt_preserves_unknowns_and_does_not_penalize_processing_failure():
    value = sitting()
    now = datetime.datetime.now(datetime.timezone.utc)
    begin(value, [1, 2, 3], now=now)
    attempts = [
        Item(
            id=1,
            question_id=1,
            answer_saved_at=now,
            audio_url="private-key",
            transcription=None,
        )
    ]
    result = receipt(
        value,
        [Item(id=i, status="pending") for i in (1, 2, 3)],
        attempts,
        now=value.answer_deadline_at + datetime.timedelta(seconds=1),
    )
    assert result["answered"] == 1
    assert result["unreached"] == 2
    assert result["reason"] == "time_limit"


@pytest.mark.parametrize("ids", [(1, 2), (1, 2, 3, 4), (1, 1, 2)])
def test_receipt_rejects_missing_extra_or_duplicate_question_set_after_deadline(ids):
    value = sitting()
    value.answer_deadline_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    with pytest.raises(fastapi.HTTPException) as error:
        receipt(value, [Item(id=i, status="pending") for i in ids], [])
    assert error.value.status_code == 409


def test_receipt_rejects_foreign_interview_question_set():
    value = sitting()
    with pytest.raises(fastapi.HTTPException):
        receipt(value, [Item(id=i, status="skipped", interview_id=99) for i in (1, 2, 3)], [])


def test_receipt_does_not_count_foreign_interview_attempt():
    value = sitting()
    value.answer_deadline_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    result = receipt(value, [Item(id=i, status="pending") for i in (1, 2, 3)],
        [Item(id=1, question_id=1, interview_id=99, audio_url="foreign")])
    assert result["answered"] == 0 and result["unreached"] == 3
