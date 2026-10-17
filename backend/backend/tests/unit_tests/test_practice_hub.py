import datetime
from types import SimpleNamespace

import pydantic
import pytest

from src.models.schemas.interview import CompletePracticeDrillRequest
from src.services.practice_hub import (
    _streak,
    has_evidence,
    infer_focus,
)


def test_focus_is_hidden_until_three_transcript_backed_answers_exist():
    report = {
        "overallFeedback": {
            "speechFluency": {
                "actionableSteps": [{"title": "Slow your pace and use clear pauses"}]
            }
        }
    }

    assert infer_focus([report], evidence_count=2) is None


def test_focus_uses_supported_action_item_and_fixed_module_link():
    report = {
        "overallFeedback": {
            "speechFluency": {
                "actionableSteps": [
                    {
                        "title": "Reduce filler words",
                        "description": "Pause before beginning the next idea.",
                    }
                ]
            }
        }
    }

    focus = infer_focus([report], evidence_count=4)

    assert focus is not None
    assert focus.module == "pace"
    assert focus.href == "/control-your-pace"
    assert focus.evidence_answer_count == 4
    assert "4 answered questions" in focus.reason


def test_focus_does_not_invent_a_recommendation_without_a_supported_signal():
    high_scores = {
        "scoreSummary": {
            "knowledgeCompetence": {"percentage": 90},
            "speechAndStructure": {"percentage": 88},
        }
    }

    assert infer_focus([high_scores], evidence_count=5) is None


def test_only_saved_nonempty_transcripts_count_as_evidence():
    saved = datetime.datetime.now(datetime.timezone.utc)

    assert has_evidence(
        SimpleNamespace(answer_saved_at=saved, transcription={"text": "A real answer"})
    )
    assert not has_evidence(
        SimpleNamespace(answer_saved_at=saved, transcription={"text": "  "})
    )
    assert not has_evidence(
        SimpleNamespace(answer_saved_at=None, transcription={"text": "Unsaved"})
    )


def test_streak_uses_india_calendar_days_and_allows_yesterday_as_latest_day():
    now = datetime.datetime(2026, 10, 17, 4, 0, tzinfo=datetime.timezone.utc)
    activity = [
        datetime.datetime(2026, 10, 16, 12, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 10, 15, 12, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 10, 14, 12, 0, tzinfo=datetime.timezone.utc),
    ]

    assert _streak(activity, now) == 3


def test_drill_completion_rejects_empty_client_evidence():
    with pytest.raises(pydantic.ValidationError):
        CompletePracticeDrillRequest(
            module="warm_up",
            level="beginner",
            client_completion_id="warm_up:valid-client-id",
            evidence_kind="text",
            evidence_units=1,
        )


def test_drill_completion_rejects_mismatched_module_evidence():
    with pytest.raises(pydantic.ValidationError):
        CompletePracticeDrillRequest(
            module="pronunciation",
            level="beginner",
            client_completion_id="pronunciation:valid-client-id",
            evidence_kind="audio",
            evidence_units=4_000,
        )
