import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy.dialects import postgresql
from src.services import resume_storage_delivery as service

RAW=b"synthetic resume"
HASH=hashlib.sha256(RAW).hexdigest()

def setup(monkeypatch,rows=()):
    result=SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:list(rows)))
    session=SimpleNamespace(execute=AsyncMock(return_value=result),commit=AsyncMock())
    context=MagicMock();context.__aenter__=AsyncMock(return_value=session);context.__aexit__=AsyncMock(return_value=False)
    monkeypatch.setattr(service.async_db,"get_session",lambda:context)
    return session

def row(attempts=0):
    return SimpleNamespace(user_id=1,file_sha256=HASH,payload=RAW,filename="resume.txt",content_type="text/plain",resume_text="synthetic resume",state="pending",attempts=attempts)


@pytest.mark.asyncio
async def test_staging_commits_bounded_original_bytes_and_preserves_pending_attempts(monkeypatch):
    session=setup(monkeypatch)
    await service.stage_resume_file(RAW,1,"resume.txt","text/plain","text",HASH)
    compiled=session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert compiled.params["payload"] == RAW
    assert "ON CONFLICT (user_id, file_sha256) DO UPDATE" in str(compiled)
    assert "WHERE resume_storage_job.state IN" in str(compiled)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw",[b"",b"wrong",b"a"*(5*1024*1024+1)])
async def test_invalid_staging_never_writes(monkeypatch,raw):
    session=setup(monkeypatch)
    with pytest.raises(ValueError):
        await service.stage_resume_file(raw,1,"file","text/plain","text",HASH)
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_success_clears_sensitive_payload_and_uses_skip_locked(monkeypatch):
    from src.services import s3_service
    job=row();session=setup(monkeypatch,[job]);upload=AsyncMock(return_value="key")
    monkeypatch.setattr(s3_service,"_background_upload_and_enforce_limit",upload)
    assert await service.deliver_resume_files() == 1
    assert job.state == "stored" and job.payload is None and job.resume_text is None
    assert job.attempts == 1
    assert upload.call_args.args[0] == RAW
    query=session.execute.call_args_list[1].args[0].compile(dialect=postgresql.dialect())
    assert "FOR UPDATE SKIP LOCKED" in str(query)
    assert 5 in query.params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts,state,preserved",[(0,"pending",True),(1,"pending",True),(2,"failed",False)])
async def test_storage_failures_have_bounded_retries(monkeypatch,attempts,state,preserved):
    from src.services import s3_service
    job=row(attempts);setup(monkeypatch,[job])
    monkeypatch.setattr(s3_service,"_background_upload_and_enforce_limit",AsyncMock(side_effect=RuntimeError("private")))
    await service.deliver_resume_files()
    assert job.state == state and job.attempts == attempts+1
    assert (job.payload is not None) == preserved
    assert (job.resume_text is not None) == preserved


@pytest.mark.asyncio
async def test_expiry_sweep_clears_pending_and_failed_payloads(monkeypatch):
    session=setup(monkeypatch)
    assert await service.deliver_resume_files() == 0
    query=session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
    assert query.params["payload"] is None and query.params["resume_text"] is None
    assert query.params["state"] == "expired"
    assert "resume_storage_job.expires_at <=" in str(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [{"user_id": 1}, {"fingerprint": HASH}])
async def test_partial_scope_cannot_deliver_other_accounts(monkeypatch, scope):
    session = setup(monkeypatch)
    with pytest.raises(ValueError):
        await service.deliver_resume_files(**scope)
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_complete_scope_limits_cleanup_and_delivery(monkeypatch):
    session = setup(monkeypatch)
    await service.deliver_resume_files(user_id=42, fingerprint=HASH)
    for call in session.execute.call_args_list:
        query = call.args[0].compile(dialect=postgresql.dialect())
        assert 42 in query.params.values()
        assert HASH in query.params.values()
