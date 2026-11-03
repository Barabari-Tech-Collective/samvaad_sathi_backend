from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.services import jev_practice as service
from src.services.jev_rollout import evaluate_cases, rollout_allowed
from .test_jev_rollout import cases, label_review


def result():
    return SimpleNamespace(
        scores={
            key: SimpleNamespace(score=3, confidence=0.9) for key in service.DIMENSIONS
        },
        choices={},
        nouls={"needs_review": SimpleNamespace(noul=0.1)},
    )


def test_validated_typed_practice_feedback():
    output = service.validated(result())
    assert output["overall_score"] == 75
    assert output["provider"] == "jev"
    assert "misconceptions" not in output and "examples" not in output


@pytest.mark.parametrize(
    "kind", ["missing", "extra", "nan", "bool", "bad_confidence", "bad_review"]
)
def test_invalid_practice_results(kind):
    response = result()
    if kind == "missing":
        del response.scores["correctness"]
    elif kind == "extra":
        response.choices["unknown"] = True
    elif kind == "nan":
        response.scores["depth"].score = float("nan")
    elif kind == "bool":
        response.scores["depth"].score = True
    elif kind == "bad_confidence":
        response.scores["depth"].confidence = -1
    else:
        response.nouls["needs_review"].noul = 2
    with pytest.raises(ValueError):
        service.validated(response)


def test_low_confidence_or_ambiguous_feedback_retains_existing_provider():
    response = result()
    response.scores["coverage"].confidence = 0.5
    assert service.validated(response) is None
    response = result()
    response.nouls["needs_review"].noul = 0.8
    assert service.validated(response) is None


@pytest.mark.asyncio
async def test_formal_assessment_never_calls_jev(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("No formal assessment requests")

    monkeypatch.setattr(service, "rollout_allowed", forbidden)
    assert (
        await service.feedback(
            interview=SimpleNamespace(interview_mode="assessment"),
            attempt_id=1,
            question="Test",
            rubric="Test",
            transcript="Test",
        )
        is None
    )


@pytest.mark.asyncio
async def test_practice_sdk_and_outage_fallback(monkeypatch):
    monkeypatch.setattr(service.settings, "JEV_API_KEY", "synthetic")
    monkeypatch.setattr(service, "rollout_allowed", lambda **kwargs: True)
    call = AsyncMock(return_value=result())

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def system_one(self, **kwargs):
            return await call(**kwargs)

    monkeypatch.setattr(service, "AsyncTypeSafeClient", Client)
    kwargs = dict(
        interview=SimpleNamespace(
            id=1, interview_mode="practice", track="Tech", difficulty="easy"
        ),
        attempt_id=1,
        question="Explain caching",
        rubric="Cache invalidation",
        transcript="Use a bounded cache",
    )
    assert (await service.feedback(**kwargs))["provider"] == "jev"
    assert call.await_args.kwargs["state"]["saved_rubric"] == "Cache invalidation"
    call.side_effect = TimeoutError("synthetic")
    assert await service.feedback(**kwargs) is None


def test_task_evaluation_is_not_interchangeable_with_ats(tmp_path):
    import json, hashlib

    report = evaluate_cases(
        cases(),
        model="test",
        dataset_id="synthetic",
        task=service.TASK,
        label_review=label_review("test", "synthetic", service.TASK),
    )
    raw = json.dumps(report).encode()
    p = tmp_path / "report.json"
    p.write_bytes(raw)
    kwargs = dict(
        path=str(p),
        digest=hashlib.sha256(raw).hexdigest(),
        model="test",
        percent=100,
        cohort_key="practice:1",
    )
    assert rollout_allowed(**kwargs, task=service.TASK)
    assert not rollout_allowed(**kwargs)
