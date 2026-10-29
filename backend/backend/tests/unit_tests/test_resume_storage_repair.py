import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
import fastapi
from starlette.datastructures import Headers
import pytest
from src.api.routes import resume
from src.services.resume_operation import validate_storage_repair

RAW = b"Synthetic original resume"
HASH = hashlib.sha256(RAW).hexdigest()
KEY = "12345678-1234-4234-8234-123456789abc"
def row(state="completed", saved=True):
    return SimpleNamespace(state=state, receipt={"saved":saved,"content_type":"text/plain"}, file_sha256=HASH)
def result(value):
    return SimpleNamespace(scalar_one_or_none=lambda:value)
def file():
    return fastapi.UploadFile(io.BytesIO(RAW),filename="resume.txt",headers=Headers({"content-type":"text/plain"}))


@pytest.mark.parametrize("state,saved", [("started",True),("failed",False),("completed",False)])
def test_unconfirmed_extractions_cannot_repair(state,saved):
    with pytest.raises(fastapi.HTTPException) as error:
        validate_storage_repair(row(state,saved),RAW,"text/plain")
    assert error.value.status_code == 409


@pytest.mark.parametrize("raw,mime,code", [(b"different","text/plain",409),(RAW,"text/html",409),(b"a"*(5*1024*1024+1),"text/plain",413)])
def test_invalid_files_rejected(raw,mime,code):
    with pytest.raises(fastapi.HTTPException) as error:
        validate_storage_repair(row(),raw,mime)
    assert error.value.status_code == code


@pytest.mark.asyncio
async def test_registered_file_does_not_upload_again(monkeypatch):
    upload=AsyncMock();monkeypatch.setattr(resume,"_background_upload_and_enforce_limit",upload)
    session=SimpleNamespace(execute=AsyncMock(side_effect=[result(row()),result(None),result(12)]))
    response=fastapi.Response()
    receipt=await resume.restore_resume_file(KEY,response,file(),SimpleNamespace(id=7),session)
    assert receipt == {"fileSha256":HASH,"status":"registered","resumeId":12}
    upload.assert_not_called()
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_storage_repair_uses_exact_bytes_without_ai(monkeypatch):
    upload=AsyncMock();monkeypatch.setattr(resume,"_background_upload_and_enforce_limit",upload)
    ai=AsyncMock();monkeypatch.setattr(resume,"extract_resume_entities_v2_with_llm",ai)
    session=SimpleNamespace(execute=AsyncMock(side_effect=[result(row()),result(None),result(None),result(12)]))
    receipt=await resume.restore_resume_file(KEY,fastapi.Response(),file(),SimpleNamespace(id=7),session)
    assert receipt["resumeId"] == 12
    assert upload.call_args.args[0] == RAW and upload.call_args.args[-1] == HASH
    ai.assert_not_called()


@pytest.mark.asyncio
async def test_storage_failure_is_not_success_and_does_not_expose_internal_error(monkeypatch):
    monkeypatch.setattr(resume,"_background_upload_and_enforce_limit",AsyncMock(side_effect=RuntimeError("private bucket credentials")))
    session=SimpleNamespace(execute=AsyncMock(side_effect=[result(row()),result(None),result(None)]))
    with pytest.raises(fastapi.HTTPException) as error:
        await resume.restore_resume_file(KEY,fastapi.Response(),file(),SimpleNamespace(id=7),session)
    assert error.value.status_code == 503
    assert "private" not in error.value.detail


@pytest.mark.asyncio
async def test_other_account_or_missing_operation_is_404(monkeypatch):
    session=SimpleNamespace(execute=AsyncMock(return_value=result(None)))
    with pytest.raises(fastapi.HTTPException) as error:
        await resume.restore_resume_file(KEY,fastapi.Response(),file(),SimpleNamespace(id=7),session)
    assert error.value.status_code == 404
    assert session.execute.call_args.args[0].compile().params == {"user_id_1":7,"request_key_1":KEY}
