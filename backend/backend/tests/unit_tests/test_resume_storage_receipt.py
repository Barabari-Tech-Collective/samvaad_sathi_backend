from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastapi
import pytest
from sqlalchemy.dialects import postgresql

from src.api.routes.resume import get_resume_storage_receipt
from src.models.schemas.resume import ResumeStorageReceipt


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_id, status", [(12, "registered"), (None, "unconfirmed")])
async def test_receipt_is_scoped_to_account_and_exact_file(resume_id, status):
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: resume_id, one_or_none=lambda: None)))
    response = fastapi.Response()
    receipt = await get_resume_storage_receipt("A" * 64, response, SimpleNamespace(id=19), session)
    query = session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
    assert query.params == {"user_id_1": 19, "file_sha256_1": "a" * 64}
    assert "user_resume.user_id =" in str(query)
    assert "user_resume.file_sha256 =" in str(query)
    assert receipt.status == status
    assert receipt.resume_id == resume_id
    assert response.headers["Cache-Control"] == "no-store"
    assert set(receipt.model_dump(by_alias=True)) == {"fileSha256", "status", "resumeId", "deliveryStatus"}


@pytest.mark.asyncio
@pytest.mark.parametrize("fingerprint", ["", "../secret", "g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"])
async def test_invalid_fingerprint_does_not_query_database(fingerprint):
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(fastapi.HTTPException) as failure:
        await get_resume_storage_receipt(fingerprint, fastapi.Response(), SimpleNamespace(id=1), session)
    assert failure.value.status_code == 422
    session.execute.assert_not_called()


def test_missing_storage_is_never_mislabeled_as_failed_or_processing():
    receipt = ResumeStorageReceipt(file_sha256="b" * 64, status="unconfirmed")
    assert receipt.resume_id is None
    assert receipt.model_dump(by_alias=True)["status"] == "unconfirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("save_fails", [False, True])
async def test_extraction_returns_fingerprint_of_exact_bytes(monkeypatch, save_fails):
    import hashlib
    import io
    from starlette.datastructures import Headers
    from src.api.routes import resume

    raw = b"Example resume\nwith whitespace  preserved in its fingerprint."
    monkeypatch.setattr(resume, "extract_resume_entities_v2_with_llm", AsyncMock(return_value=({"skills": ["python"]}, None, 1, "synthetic")))
    repo = SimpleNamespace(update_resume_data=AsyncMock(side_effect=RuntimeError("db failure") if save_fails else None))
    from src.services import resume_storage_delivery
    stage = AsyncMock()
    monkeypatch.setattr(resume_storage_delivery,"stage_resume_file",stage)
    tasks = fastapi.BackgroundTasks()
    result = await resume.extract_resume(
        tasks,
        fastapi.UploadFile(io.BytesIO(raw), filename="example.txt", headers=Headers({"content-type": "text/plain"})),
        SimpleNamespace(id=19), repo,
    )
    assert result["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["saved"] is not save_fails
    assert len(tasks.tasks) == (0 if save_fails else 1)
    if not save_fails:
        assert stage.call_args.args[-1] == result["file_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state,hours,expected", [
    ("pending", 1, "pending"), ("pending", -1, "expired"),
    ("failed", 1, "failed"), ("expired", -1, "expired"),
    ("stored", 1, None),
])
async def test_delivery_metadata_is_scoped_and_does_not_load_payload(state, hours, expected):
    import datetime
    job = SimpleNamespace(state=state, expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours))
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: None),
        SimpleNamespace(one_or_none=lambda: job),
    ]))
    receipt = await get_resume_storage_receipt("a" * 64, fastapi.Response(), SimpleNamespace(id=19), session)
    assert receipt.delivery_status == expected
    assert receipt.status == "unconfirmed"
    query = session.execute.call_args_list[1].args[0].compile(dialect=postgresql.dialect())
    assert query.params == {"user_id_1": 19, "file_sha256_1": "a" * 64}
    assert "payload" not in str(query) and "resume_text" not in str(query)


@pytest.mark.asyncio
async def test_registered_file_takes_precedence_over_delivery_history():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 12)))
    receipt = await get_resume_storage_receipt("a" * 64, fastapi.Response(), SimpleNamespace(id=19), session)
    assert receipt.status == "registered" and receipt.delivery_status is None
    session.execute.assert_awaited_once()
