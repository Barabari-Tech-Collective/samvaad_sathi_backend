from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.routes import interviews
from src.models.schemas.interview import ResumeInterviewRequest


def question(question_id: int, order: int):
    return SimpleNamespace(
        id=question_id,
        order=order,
        text=f"Question {order}",
        topic="communication",
        category="behavioural",
        status="pending",
        resume_used=False,
        is_follow_up=False,
        parent_question_id=None,
        follow_up_strategy=None,
    )


@pytest.mark.asyncio
async def test_resume_returns_full_snapshot_and_first_unsaved_checkpoint(monkeypatch):
    all_questions = [question(11, 1), question(12, 2), question(13, 3)]
    interview = SimpleNamespace(
        id=7,
        user_id=42,
        track="general",
        difficulty="beginner",
        status="active",
        interview_mode="practice",
    )
    attempts = [
        SimpleNamespace(
            id=101,
            question_id=11,
            answer_saved_at=object(),
            answer_version=2,
            answer_kind="audio",
            transcription_status="processing",
        ),
        SimpleNamespace(
            id=102,
            question_id=12,
            answer_saved_at=None,
            answer_version=0,
            answer_kind=None,
            transcription_status="pending",
        ),
    ]
    interview_repo = SimpleNamespace(
        get_by_id_and_user=AsyncMock(return_value=interview)
    )
    question_repo = SimpleNamespace(
        list_by_interview=AsyncMock(return_value=all_questions),
        async_session=SimpleNamespace(),
    )
    attempt_repo = SimpleNamespace(list_by_interview=AsyncMock(return_value=attempts))
    monkeypatch.setattr(interviews, "_backfill_follow_up_parents", AsyncMock())
    monkeypatch.setattr(interviews, "_get_supplement_map", AsyncMock(return_value={}))

    result = await interviews.resume_interview(
        ResumeInterviewRequest(interviewId=7),
        current_user=SimpleNamespace(id=42),
        interview_repo=interview_repo,
        question_repo=question_repo,
        question_attempt_repo=attempt_repo,
    )

    assert [item.interview_question_id for item in result.questions] == [11, 12, 13]
    assert result.attempted_questions == 2
    assert result.answered_questions == 1
    assert result.remaining_questions == 2
    assert result.resume_question_index == 1
    assert result.questions[0].question_attempt_id == 101
    assert result.questions[0].answer_saved is True
    assert result.questions[1].question_attempt_id == 102
    assert result.questions[1].answer_saved is False
    assert result.questions[2].question_attempt_id is None


@pytest.mark.asyncio
async def test_resume_reports_all_answers_saved_without_inventing_a_next_question(
    monkeypatch,
):
    all_questions = [question(11, 1)]
    interview = SimpleNamespace(
        id=7,
        user_id=42,
        track="general",
        difficulty="beginner",
        status="active",
        interview_mode="practice",
    )
    attempts = [
        SimpleNamespace(
            id=101,
            question_id=11,
            answer_saved_at=object(),
            answer_version=1,
            answer_kind="text",
            transcription_status="transcribed",
        )
    ]
    monkeypatch.setattr(interviews, "_backfill_follow_up_parents", AsyncMock())
    monkeypatch.setattr(interviews, "_get_supplement_map", AsyncMock(return_value={}))

    result = await interviews.resume_interview(
        ResumeInterviewRequest(interviewId=7),
        current_user=SimpleNamespace(id=42),
        interview_repo=SimpleNamespace(
            get_by_id_and_user=AsyncMock(return_value=interview)
        ),
        question_repo=SimpleNamespace(
            list_by_interview=AsyncMock(return_value=all_questions),
            async_session=SimpleNamespace(),
        ),
        question_attempt_repo=SimpleNamespace(
            list_by_interview=AsyncMock(return_value=attempts)
        ),
    )

    assert result.resume_question_index is None
    assert result.remaining_questions == 0
    assert result.questions[0].answer_kind == "text"
