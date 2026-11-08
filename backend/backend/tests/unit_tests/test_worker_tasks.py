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


class _Session:
    def __init__(self, interview):
        self.interview, self.committed, self.closed = interview, False, False

    async def get(self, model, key):
        return self.interview if model.__name__ == "Interview" else None

    async def commit(self):
        self.committed = True

    async def close(self):
        self.closed = True


def _report_patches(monkeypatch, session, attempts):
    from types import SimpleNamespace

    from src.repository.crud import interview_question, question, summary_report
    from src.services import analysis, summary_report_v2
    from src.worker import database

    saved = {}

    async def listing(self, interview_id):
        return attempts

    async def none(self, interview_id):
        return []

    async def analyzed(**kwargs):
        return 0

    async def build(self, *args):
        return {"reportStatus": "ready"}

    async def upsert(self, interview_id, report_json):
        saved[interview_id] = report_json

    monkeypatch.setattr(database, "get_session", lambda: session)
    monkeypatch.setattr(question.QuestionAttemptCRUDRepository, "list_by_interview", listing)
    monkeypatch.setattr(interview_question.InterviewQuestionCRUDRepository, "list_by_interview", none)
    monkeypatch.setattr(analysis.analysis_service, "ensure_recorded_attempts_analyzed", analyzed)
    monkeypatch.setattr(summary_report_v2.SummaryReportServiceV2, "generate_for_interview_lite", build)
    monkeypatch.setattr(summary_report.SummaryReportCRUDRepository, "upsert", upsert)
    return saved, SimpleNamespace


@pytest.mark.asyncio
async def test_report_task_waits_while_an_answer_is_being_read(monkeypatch):
    from types import SimpleNamespace

    session = _Session(SimpleNamespace(id=5, user_id=1, track="x"))
    busy = SimpleNamespace(answer_saved_at="now", transcription_status="processing")
    saved, _ = _report_patches(monkeypatch, session, [busy])
    with pytest.raises(Retry):
        await tasks_module.generate_summary_report_task({"job_try": 1}, interview_id=5)
    assert not saved and session.closed


@pytest.mark.asyncio
async def test_report_task_builds_and_saves(monkeypatch):
    from types import SimpleNamespace

    session = _Session(SimpleNamespace(id=5, user_id=1, track="x"))
    done = SimpleNamespace(answer_saved_at="now", transcription_status="transcribed")
    saved, _ = _report_patches(monkeypatch, session, [done])
    result = await tasks_module.generate_summary_report_task({"job_try": 1}, interview_id=5)
    assert result["status"] == "done" and saved == {5: {"reportStatus": "ready"}} and session.committed
