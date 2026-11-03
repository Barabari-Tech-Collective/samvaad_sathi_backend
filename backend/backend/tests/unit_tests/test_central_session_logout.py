import httpx
import pytest
from fastapi import HTTPException
from src.services import central_session_logout as subject

@pytest.mark.asyncio
async def test_global_logout_requires_positive_ack(monkeypatch):
    monkeypatch.setattr(subject.settings, 'AUTH_SERVICE_BASE_URL', 'https://auth.example.test')
    seen=[]
    def handler(req):
        seen.append(req)
        return httpx.Response(200,json={'isSuccess':True})
    await subject.revoke_everywhere('synthetic',transport=httpx.MockTransport(handler))
    assert seen[0].url.path == '/barabari-auth/api/auth/public/v2/logout-all'
    assert seen[0].headers['authorization'] == 'Bearer synthetic'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,body',[(200,{}),(200,{'isSuccess':False}),(401,{}),(403,{}),(302,{}),(500,{})])
async def test_failure_never_claims_revocation(monkeypatch,status,body):
    monkeypatch.setattr(subject.settings, 'AUTH_SERVICE_BASE_URL', 'https://auth.example.test')
    with pytest.raises(HTTPException) as error:
        await subject.revoke_everywhere('synthetic',transport=httpx.MockTransport(lambda _:httpx.Response(status,json=body)))
    assert error.value.status_code == (401 if status == 401 else 503)

@pytest.mark.asyncio
@pytest.mark.parametrize('url',['http://auth.example.test','https://user:password@auth.test','https://auth.test/?secret=x'])
async def test_insecure_or_ambiguous_config_rejected(monkeypatch,url):
    monkeypatch.setattr(subject.settings,'AUTH_SERVICE_BASE_URL',url)
    with pytest.raises(HTTPException):
        await subject.revoke_everywhere('synthetic')
