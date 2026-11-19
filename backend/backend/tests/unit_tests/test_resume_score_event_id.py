import asyncio
import json
import uuid
from pathlib import Path

import httpx

from src.services import barabari_integration as bi

SCHEMA = json.loads((Path(__file__).parents[1] / "fixtures/contracts/resume-score.v1.request.schema.json").read_text())


def _capture(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            seen["body"] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(bi.settings, "SAMPARK_SAATHI_API_KEY", "k", raising=False)
    monkeypatch.setattr(bi.settings, "SAMPARK_SAATHI_BASE_URL", "http://sampark", raising=False)
    monkeypatch.setattr(bi.httpx, "AsyncClient", FakeClient)
    return seen


def _matches_contract(body: dict) -> None:
    allowed = set(SCHEMA["properties"])
    assert set(body) <= allowed, set(body) - allowed
    for key in SCHEMA["required"]:
        assert key in body


def test_event_id_is_sent_and_body_matches_the_contract(monkeypatch):
    seen = _capture(monkeypatch)
    eid = str(uuid.uuid4())
    asyncio.run(bi.submit_resume_score_to_barabari("STU-1", 70, "r", event_id=eid))
    assert seen["body"]["eventId"] == eid
    _matches_contract(seen["body"])


def test_without_event_id_the_body_is_unchanged(monkeypatch):
    seen = _capture(monkeypatch)
    asyncio.run(bi.submit_resume_score_to_barabari("STU-1", 70, "r"))
    assert "eventId" not in seen["body"]
    _matches_contract(seen["body"])


def test_the_task_passes_the_same_event_id_on_every_attempt(monkeypatch):
    from src.worker import tasks

    calls = []

    async def fake_submit(**kwargs):
        calls.append(kwargs["event_id"])

    monkeypatch.setattr(tasks, "submit_resume_score_to_barabari", fake_submit)
    for attempt in (1, 2):
        asyncio.run(tasks.submit_resume_score_task({"job_try": attempt}, student_id="S", resume_score=1, request_id="r", event_id="E1"))
    assert calls == ["E1", "E1"]
