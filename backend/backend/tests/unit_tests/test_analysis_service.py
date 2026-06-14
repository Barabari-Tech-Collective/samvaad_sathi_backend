from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.models.db.question_attempt import QuestionAttempt
from src.services.analysis import AnalysisAggregationService


class DummyMetadata(SimpleNamespace):
    pass


class DummyQA(SimpleNamespace):
    pass


@pytest.mark.asyncio
async def test_ensure_recorded_attempts_analyzed_only_fills_missing_sections():
    service = AnalysisAggregationService()
    calls: list[dict] = []

    async def fake_aggregate_question_analysis(**kwargs):
        calls.append(kwargs)
        return (
            None,
            DummyMetadata(failed_analyses=[]),
            True,
            None,
        )

    service.aggregate_question_analysis = fake_aggregate_question_analysis  # type: ignore[method-assign]

    needs_analysis = DummyQA(
        id=10,
        transcription={"text": "This is my recorded answer."},
        analysis_json={"domain": {"domain_score": 80}},
    )
    complete = DummyQA(
        id=11,
        transcription={"text": "Already complete."},
        analysis_json={
            "domain": {"domain_score": 80},
            "communication": {"communication_score": 75},
            "pace": {"pace_score": 90},
            "pause": {"pause_score": 70},
        },
    )
    no_recorded_answer = DummyQA(
        id=12,
        transcription=None,
        analysis_json=None,
    )

    analyzed_count = await service.ensure_recorded_attempts_analyzed(
        question_attempts=[
            cast(QuestionAttempt, needs_analysis),
            cast(QuestionAttempt, complete),
            cast(QuestionAttempt, no_recorded_answer),
        ],
        user_id=99,
        db=cast(SQLAlchemyAsyncSession, None),
    )

    assert analyzed_count == 1
    assert len(calls) == 1
    assert calls[0]["question_attempt_id"] == 10
    assert calls[0]["analysis_types"] == ["communication", "pace", "pause"]
