from types import SimpleNamespace

import pytest

from src.api.routes.analysis import analyze_pace, analyze_pause
from src.models.schemas.analysis import PaceAnalysisRequest, PauseAnalysisRequest


class _FakeQuestionRepo:
    """Minimal stand-in for QuestionAttemptCRUDRepository."""

    def __init__(self, qa):
        self._qa = qa
        self.last_persisted = None

    async def get_by_id_and_user(self, *, question_attempt_id, user_id):
        return self._qa

    async def update_analysis_json(self, *, question_attempt_id, analysis_json):
        self.last_persisted = analysis_json
        self._qa.analysis_json = analysis_json
        return self._qa


def _make_words(count: int) -> list[dict]:
    return [{"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(count)]


@pytest.mark.asyncio
async def test_analyze_pace_does_not_score_negligible_speech():
    """Regression guard: a transcription with only 1-2 word-level timestamps
    (e.g. a Whisper hallucination from near-silent audio) must not produce a
    plausible-looking pace score — see src/api/routes/analysis.py
    analyze_pace."""
    qa = SimpleNamespace(
        transcription={"words": _make_words(2)},
        analysis_json={},
    )
    repo = _FakeQuestionRepo(qa)
    current_user = SimpleNamespace(id=1)

    response = await analyze_pace(
        request=PaceAnalysisRequest(question_attempt_id=1),
        current_user=current_user,
        question_repo=repo,
    )

    assert response.pace_score == 0.0
    assert response.pace_category == "insufficient_speech"
    assert repo.last_persisted["pace"]["pace_category"] == "insufficient_speech"


@pytest.mark.asyncio
async def test_analyze_pace_still_scores_real_speech():
    """A real answer (well above the word-count floor) should still be
    scored normally, not caught by the near-empty guard."""
    qa = SimpleNamespace(
        transcription={"words": _make_words(30)},
        analysis_json={},
    )
    repo = _FakeQuestionRepo(qa)
    current_user = SimpleNamespace(id=1)

    response = await analyze_pace(
        request=PaceAnalysisRequest(question_attempt_id=1),
        current_user=current_user,
        question_repo=repo,
    )

    assert response.pace_category != "insufficient_speech"


@pytest.mark.asyncio
async def test_analyze_pause_does_not_score_negligible_speech():
    qa = SimpleNamespace(
        transcription={"words": _make_words(1)},
        analysis_json={},
    )
    repo = _FakeQuestionRepo(qa)
    current_user = SimpleNamespace(id=1)

    response = await analyze_pause(
        request=PauseAnalysisRequest(question_attempt_id=1),
        current_user=current_user,
        question_repo=repo,
    )

    assert response.pause_score == 0.0
    assert "not enough speech" in response.overview.lower()
