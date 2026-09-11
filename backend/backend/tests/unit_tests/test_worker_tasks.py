import pytest
from arq.worker import Retry

from src.worker import tasks as tasks_module


@pytest.mark.asyncio
async def test_submit_resume_score_task_success_does_not_retry(monkeypatch):
    called = {}

    async def _fake_submit(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(tasks_module, "submit_resume_score_to_barabari", _fake_submit)

    await tasks_module.submit_resume_score_task(
        {"job_try": 1},
        student_id="s1",
        resume_score=80,
        request_id="req-1",
        target_role="Backend Engineer",
    )

    assert called == {
        "student_id": "s1",
        "resume_score": 80,
        "request_id": "req-1",
        "target_role": "Backend Engineer",
    }


@pytest.mark.asyncio
async def test_submit_resume_score_task_converts_failure_to_retry_with_backoff(monkeypatch):
    async def _fake_submit(**kwargs):
        raise ConnectionError("Barabari unreachable")

    monkeypatch.setattr(tasks_module, "submit_resume_score_to_barabari", _fake_submit)

    with pytest.raises(Retry) as exc_info:
        await tasks_module.submit_resume_score_task(
            {"job_try": 2},
            student_id="s1",
            resume_score=80,
            request_id="req-1",
        )

    # defer_score is stored in milliseconds; job_try=2 -> defer(4s) = 4000ms
    assert exc_info.value.defer_score == 4000
