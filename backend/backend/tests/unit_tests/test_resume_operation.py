from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from src.services import resume_operation as service

KEY = "12345678-1234-4234-8234-123456789abc"
HASH = "a" * 64


def session_fixture(monkeypatch, claimed, row=None):
    result = SimpleNamespace(scalar_one_or_none=lambda: claimed, scalar_one=lambda: row)
    session = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(service.async_db, "get_session", lambda: context)
    return session


@pytest.mark.asyncio
async def test_admission_commits_before_work_and_terminal_receipt(monkeypatch):
    session = session_fixture(monkeypatch, KEY)
    async def work():
        assert session.commit.await_count == 1
        return {"saved": True, "text_length": 10}
    receipt = await service.run_resume_operation(1, KEY, HASH, work)
    assert receipt["saved"] is True
    assert session.commit.await_count == 2
    statement = session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
    assert "ON CONFLICT (user_id, request_key) DO NOTHING" in str(statement)
    assert statement.params["user_id"] == 1


@pytest.mark.asyncio
async def test_completed_retry_replays_without_work(monkeypatch):
    row = SimpleNamespace(file_sha256=HASH, state="completed", receipt={"saved": True})
    session_fixture(monkeypatch, None, row)
    work = AsyncMock()
    assert await service.run_resume_operation(1, KEY, HASH, work) == {"saved": True}
    work.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state,fingerprint", [("started", HASH), ("failed", HASH), ("completed", "b" * 64)])
async def test_unresolved_failed_or_mismatched_retry_never_executes(monkeypatch, state, fingerprint):
    session_fixture(monkeypatch, None, SimpleNamespace(file_sha256=fingerprint, state=state, receipt={"saved": True}))
    work = AsyncMock()
    with pytest.raises(HTTPException) as failure:
        await service.run_resume_operation(1, KEY, HASH, work)
    assert failure.value.status_code == 409
    work.assert_not_called()


@pytest.mark.asyncio
async def test_work_failure_persists_sanitized_state(monkeypatch):
    session = session_fixture(monkeypatch, KEY)
    with pytest.raises(RuntimeError):
        await service.run_resume_operation(1, KEY, HASH, AsyncMock(side_effect=RuntimeError("private")))
    statement = session.execute.call_args_list[-1].args[0].compile(dialect=postgresql.dialect())
    assert statement.params["state"] == "failed"
    assert statement.params["receipt"] is None
    assert "private" not in str(statement.params)


@pytest.mark.parametrize("key", ["bad", "", "../other"])
def test_invalid_keys(key):
    with pytest.raises(HTTPException) as failure:
        service.operation_key(key)
    assert failure.value.status_code == 422


@pytest.mark.asyncio
async def test_status_is_account_scoped_and_does_not_expose_receipt():
    from src.api.routes.resume import get_resume_operation
    from fastapi import Response
    row = SimpleNamespace(state="completed", file_sha256=HASH, receipt={"saved":True,"preview":"private"})
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)))
    response = Response()
    result = await get_resume_operation(KEY, response, SimpleNamespace(id=7), session)
    statement = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert statement.params == {"user_id_1":7,"request_key_1":KEY}
    assert result == {"operationKey":KEY,"state":"completed","fileSha256":HASH,"dataSaved":True}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("state,receipt", [("started", None), ("completed", {"saved":True}), ("completed", None), ("completed", {}), ("unknown", None)])
def test_recovery_never_restarts_ambiguous_or_saved_work(state, receipt):
    row = SimpleNamespace(request_key=KEY, state=state, receipt=receipt)
    with pytest.raises(HTTPException) as failure:
        service.recovery_key(1, row)
    assert failure.value.status_code == 409


@pytest.mark.parametrize("state,receipt", [("failed", None), ("completed", {"saved":False})])
def test_explicit_recovery_has_one_stable_account_bound_child_key(state, receipt):
    row = SimpleNamespace(request_key=KEY, state=state, receipt=receipt)
    child = service.recovery_key(1, row)
    assert child != KEY
    assert child == service.recovery_key(1, row)
    assert child != service.recovery_key(2, row)


@pytest.mark.asyncio
async def test_recovery_endpoint_is_scoped_and_does_not_execute_work():
    from src.api.routes.resume import prepare_resume_recovery
    from fastapi import Response
    row = SimpleNamespace(request_key=KEY, state="failed", receipt=None, file_sha256=HASH)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda:row)))
    response = Response()
    result = await prepare_resume_recovery(KEY, response, SimpleNamespace(id=7), session)
    statement = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert statement.params == {"user_id_1":7,"request_key_1":KEY}
    assert result["fileSha256"] == HASH and result["operationKey"] != KEY
    assert response.headers["Cache-Control"] == "no-store"
    session.execute.assert_awaited_once()
