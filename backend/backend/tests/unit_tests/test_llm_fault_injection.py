"""When the AI provider times out or answers with junk, nothing breaks and no score is invented.

Covers the testing-strategy gap "AI provider failure": a timeout, non-JSON text and
JSON in the wrong shape must each come back as an error the caller can show as a
recoverable state, with no score and no exception.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from src.services import llm


class _Completions:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    async def create(self, **_kwargs):
        if isinstance(self.behaviour, BaseException):
            raise self.behaviour
        message = SimpleNamespace(content=self.behaviour)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _client(behaviour):
    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions(behaviour)))


FAULTS = {
    "timeout": httpx.ReadTimeout("provider timed out"),
    "connection-reset": httpx.ConnectError("connection reset"),
    "not-json": "Sure! Here is the score you asked for: 87",
    "wrong-shape": '{"score": "very good"}',
    "empty": "",
}


@pytest.fixture
def provider(monkeypatch):
    def use(behaviour):
        monkeypatch.setattr(llm, "get_active_llm_model_and_key", lambda: ("test-model", "key"))
        monkeypatch.setattr(llm, "get_llm_client", lambda: _client(behaviour))

    return use


@pytest.mark.parametrize("fault", FAULTS, ids=list(FAULTS))
@pytest.mark.asyncio
async def test_domain_scoring_reports_the_failure_and_invents_no_score(provider, fault):
    provider(FAULTS[fault])
    analysis, error, _latency, _model = await asyncio.wait_for(
        llm.analyze_domain_with_llm(
            user_profile={"target_position": "Data analyst"},
            question_text="Explain a JOIN in SQL.",
            transcription="A join combines rows from two tables using a related column between them.",
        ),
        timeout=5,
    )
    assert analysis.get("overall_score") is None
    assert error


@pytest.mark.parametrize("fault", FAULTS, ids=list(FAULTS))
@pytest.mark.asyncio
async def test_follow_up_failure_publishes_nothing(provider, fault):
    provider(FAULTS[fault])
    question, error, _latency, _model = await llm.generate_follow_up_question(
        track="general",
        difficulty="medium",
        base_question="Tell me about a project.",
        answer_excerpt="I led a team that rebuilt our billing system last year.",
    )
    assert question is None
    assert error
