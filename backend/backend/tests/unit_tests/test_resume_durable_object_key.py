import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from src.services import s3_service
from src.repository.database import async_db

RAW = b'synthetic stable bytes'
SHA = hashlib.sha256(RAW).hexdigest()


def storage(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(s3_service,'s3_client',client)
    monkeypatch.setattr(s3_service,'BUCKET_NAME','synthetic-unit-bucket')
    return client


def test_stable_key_ignores_filename_but_is_account_scoped(monkeypatch):
    client = storage(monkeypatch)
    first = s3_service.upload_resume_to_s3(RAW,7,'a.txt','text/plain',SHA)
    assert first == s3_service.upload_resume_to_s3(RAW,7,'renamed.txt','text/plain',SHA)
    assert first != s3_service.upload_resume_to_s3(RAW,8,'a.txt','text/plain',SHA)
    assert client.put_object.call_args.kwargs['Body'] == RAW


def test_mismatched_fingerprint_never_writes_an_object(monkeypatch):
    client = storage(monkeypatch)
    with pytest.raises(ValueError):
        s3_service.upload_resume_to_s3(RAW,7,'a.txt','text/plain','a'*64)
    client.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_database_commit_does_not_delete_shared_stable_key(monkeypatch):
    client = storage(monkeypatch)
    result = SimpleNamespace(scalar_one_or_none=lambda:None,scalars=lambda:SimpleNamespace(all=lambda:[]))
    session = SimpleNamespace(execute=AsyncMock(return_value=result),add=MagicMock(),flush=AsyncMock(),commit=AsyncMock(side_effect=RuntimeError('ambiguous commit')))
    context=MagicMock();context.__aenter__=AsyncMock(return_value=session);context.__aexit__=AsyncMock(return_value=False)
    monkeypatch.setattr(async_db,'get_session',lambda:context)
    with pytest.raises(RuntimeError,match='ambiguous commit'):
        await s3_service._background_upload_and_enforce_limit(RAW,7,'a.txt','text/plain','synthetic','onboarding',len(RAW),SHA,durable_delivery=True)
    client.put_object.assert_called_once()
    client.delete_object.assert_not_called()


@pytest.mark.asyncio
async def test_durable_delivery_requires_verified_fingerprint(monkeypatch):
    client = storage(monkeypatch)
    with pytest.raises(ValueError):
        await s3_service._background_upload_and_enforce_limit(RAW,7,'a.txt','text/plain','synthetic','onboarding',durable_delivery=True)
    client.put_object.assert_not_called()
