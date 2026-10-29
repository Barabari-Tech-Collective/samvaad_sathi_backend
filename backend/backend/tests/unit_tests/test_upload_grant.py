from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from src.services import upload_grant as grants

@pytest.fixture
def store(monkeypatch):
    values={}
    async def put(k,v,**_):values[k]=v
    async def take(_script,_n,k):return values.pop(k,None)
    redis=SimpleNamespace(set=AsyncMock(side_effect=put),eval=AsyncMock(side_effect=take))
    monkeypatch.setattr(grants.async_redis,'client',redis)
    monkeypatch.setattr(grants,'settings',SimpleNamespace(PRODUCT_COOKIE_API_ENABLED=True,CENTRAL_IDENTITY_CHECK_ENABLED=True,PRODUCT_SESSION_ENCRYPTION_KEYS=Fernet.generate_key().decode()))
    return values,redis

@pytest.mark.asyncio
async def test_grant_is_encrypted_and_single_use(store):
    values,_=store
    grant=await grants.issue('SECRET','/api/bulk-ats/batches/1/resumes','POST')
    assert all(b'SECRET' not in v for v in values.values())
    assert await grants.consume(grant,'/api/bulk-ats/batches/1/resumes','POST')=='SECRET'
    with pytest.raises(HTTPException):await grants.consume(grant,'/api/bulk-ats/batches/1/resumes','POST')

@pytest.mark.asyncio
@pytest.mark.parametrize('path,method',[('/api/bulk-ats/batches/2/resumes','POST'),('/api/bulk-ats/batches/1/resumes','DELETE'),('/api/auth/profile','GET')])
async def test_wrong_operation_consumes_and_rejects_grant(store,path,method):
    grant=await grants.issue('SECRET','/api/bulk-ats/batches/1/resumes','POST')
    with pytest.raises(HTTPException):await grants.consume(grant,path,method)
    with pytest.raises(HTTPException):await grants.consume(grant,'/api/bulk-ats/batches/1/resumes','POST')

@pytest.mark.asyncio
async def test_unregistered_path_cannot_receive_grant(store):
    with pytest.raises(HTTPException):await grants.issue('SECRET','/api/owner/delete','POST')
    store[1].set.assert_not_awaited()

@pytest.mark.asyncio
async def test_expired_grant_rejected(store):
    with pytest.raises(HTTPException):await grants.consume('upload_grant_'+'a'*43,'/api/bulk-ats/batches/1/resumes','POST')

@pytest.mark.asyncio
async def test_disabled_configuration_cannot_issue(store):
    grants.settings.PRODUCT_COOKIE_API_ENABLED=False
    with pytest.raises(HTTPException):await grants.issue('SECRET','/api/bulk-ats/batches/1/resumes','POST')
