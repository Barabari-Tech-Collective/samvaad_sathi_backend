import asyncio
import json
from types import SimpleNamespace

import fastapi
import httpx
import pytest

from src.services import whatsapp_updates as service


@pytest.fixture(autouse=True)
def connection(monkeypatch):
    monkeypatch.setattr(service, "batches_connection", lambda: ("http://sampark", "k"))


def _transport(seen, status=200, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("SAMVAAD-SAATHI-API-KEY"), request.content))
        return httpx.Response(status, json=body if body is not None else {"isSuccess": True, "data": {"on": True, "decidedAt": None}})
    return httpx.MockTransport(handler)


STUDENT = SimpleNamespace(student_id="STU-1")


def test_reads_the_setting_with_the_service_key():
    seen = []
    out = asyncio.run(service.get(STUDENT, transport=_transport(seen)))
    assert out == {"on": True, "decided_at": None}
    assert seen[0][:3] == ("GET", "/samvaad-saathi/v1/students/STU-1/whatsapp-updates", "k")


def test_turning_off_sends_on_false():
    seen = []
    body = {"isSuccess": True, "data": {"on": False, "decidedAt": "2026-11-19T10:00:00Z"}}
    out = asyncio.run(service.put(STUDENT, False, transport=_transport(seen, body=body)))
    assert out["on"] is False
    assert seen[0][0] == "PUT" and json.loads(seen[0][3]) == {"on": False}


def test_not_a_barabari_student_is_404():
    with pytest.raises(fastapi.HTTPException) as e:
        asyncio.run(service.get(SimpleNamespace(student_id=None)))
    assert e.value.status_code == 404


@pytest.mark.parametrize("status,body", [(500, {}), (200, {"isSuccess": True, "data": {"on": "yes"}})])
def test_sampark_problems_become_a_plain_503(status, body):
    with pytest.raises(fastapi.HTTPException) as e:
        asyncio.run(service.put(STUDENT, True, transport=_transport([], status=status, body=body)))
    assert e.value.status_code == 503
    assert e.value.detail == service.NOT_SAVED
