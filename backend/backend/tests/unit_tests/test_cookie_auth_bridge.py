from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI, Depends, HTTPException
from src.api.dependencies import auth, product_session
from src.api.routes.product_session import COOKIE


@pytest.fixture
async def setup(monkeypatch):
    config=SimpleNamespace(PRODUCT_COOKIE_API_ENABLED=True)
    monkeypatch.setattr(auth,'settings',config)
    lookup=AsyncMock(return_value=SimpleNamespace(id=7))
    monkeypatch.setattr(product_session,'get_session_user',lookup)
    app=FastAPI()
    @app.get('/protected')
    async def protected(user=Depends(auth.get_current_user)): return {'id':user.id}
    @app.post('/protected')
    async def write(user=Depends(auth.get_current_user)): return {'id':user.id}
    @app.get('/optional')
    async def optional(user=Depends(auth.get_optional_current_user)): return {'id':user.id if user else None}
    for route in app.routes:
        if getattr(route,'path','') in ['/protected','/optional']:
            for dep in route.dependant.dependencies:
                for child in dep.dependencies:
                    if child.name=='user_repo': app.dependency_overrides[child.call]=lambda:object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://api.test') as client:
        yield client,config,lookup


@pytest.mark.asyncio
async def test_cookie_read_delegates_to_guarded_session_dependency(setup):
    client,_,lookup=setup
    response=await client.get('/protected',headers={'cookie':f'{COOKIE}=opaque'})
    assert response.json()=={'id':7}
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cookie_write_preserves_csrf_dependency_error(setup):
    client,_,lookup=setup;lookup.side_effect=HTTPException(403,'CSRF required')
    response=await client.post('/protected',headers={'cookie':f'{COOKIE}=opaque'})
    assert response.status_code==403


@pytest.mark.asyncio
@pytest.mark.parametrize('header',['Bearer other','Basic other','invalid'])
async def test_mixed_auth_is_rejected_without_account_selection(setup,header):
    client,_,lookup=setup
    response=await client.get('/protected',headers={'cookie':f'{COOKIE}=opaque','authorization':header})
    assert response.status_code==400
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_cookie_bridge_disabled_by_default_gate(setup):
    client,config,lookup=setup;config.PRODUCT_COOKIE_API_ENABLED=False
    response=await client.get('/protected',headers={'cookie':f'{COOKIE}=opaque'})
    assert response.status_code==403;lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_credentials_remains_unauthenticated(setup):
    client,_,lookup=setup
    assert (await client.get('/protected')).status_code==403
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_no_credentials_remains_anonymous(setup):
    client,_,_=setup
    assert (await client.get('/optional')).json()=={'id':None}


@pytest.mark.asyncio
async def test_optional_cookie_is_authenticated(setup):
    client,_,lookup=setup
    assert (await client.get('/optional',headers={'cookie':f'{COOKIE}=opaque'})).json()=={'id':7}
    lookup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('code',[401,403,503])
async def test_optional_invalid_cookie_never_downgrades_to_anonymous(setup,code):
    client,_,lookup=setup;lookup.side_effect=HTTPException(code,'session unavailable')
    assert (await client.get('/optional',headers={'cookie':f'{COOKIE}=opaque'})).status_code==code


@pytest.mark.asyncio
async def test_cookie_cannot_use_legacy_token_minting_profile(monkeypatch):
    from starlette.requests import Request
    monkeypatch.setattr(auth,'settings',SimpleNamespace(PRODUCT_COOKIE_API_ENABLED=True))
    lookup=AsyncMock();monkeypatch.setattr(product_session,'get_session_user',lookup)
    request=Request({'type':'http','method':'GET','path':'/api/me','headers':[(b'cookie',f'{COOKIE}=opaque'.encode())],'scheme':'https','server':('api.test',443),'query_string':b''})
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(credentials=None,user_repo=object(),request=request)
    assert exc.value.status_code==409
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_dependency_chain_reuses_bearer_user_resolution(monkeypatch):
    from starlette.requests import Request
    from src.services import central_identity_access
    from src.services.product_session import Session
    monkeypatch.setattr(auth,'settings',SimpleNamespace(PRODUCT_COOKIE_API_ENABLED=True,CENTRAL_IDENTITY_CHECK_ENABLED=False))
    claims={'sub':'u@example.org','userUniqueId':'central-u','role':'STUDENT'}
    monkeypatch.setattr(auth,'decode_sso_access_token',lambda token:claims)
    monkeypatch.setattr(central_identity_access,'current_identity',AsyncMock(return_value=claims))
    monkeypatch.setattr(auth,'is_access_revoked',AsyncMock(return_value=False))
    monkeypatch.setattr(product_session,'boundary',lambda request:SimpleNamespace(read=AsyncMock(return_value=Session('central-u','access','refresh','csrf'))))
    monkeypatch.setattr(product_session,'verified_principal',AsyncMock(return_value='central-u'))
    user=SimpleNamespace(id=7,is_admin=False,is_super_admin=False)
    repo=SimpleNamespace(get_user_by_email=AsyncMock(return_value=user))
    request=Request({'type':'http','method':'GET','path':'/practice','headers':[(b'cookie',f'{COOKIE}=opaque'.encode())],'scheme':'https','server':('api.test',443),'query_string':b''})
    result=await auth.get_current_user(credentials=None,user_repo=repo,request=request)
    assert result is user and result._token_source=='central_sso'
    assert result._token_role=='STUDENT'
    repo.get_user_by_email.assert_awaited_once_with(email='u@example.org')

@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v2/graduation/me", "/api/v2/practice-dashboard/consents/me", "/api/v2/human-interviews/me"])
async def test_feature_me_routes_use_cookie_session_without_legacy_profile_block(monkeypatch, path):
    from starlette.requests import Request
    monkeypatch.setattr(auth, 'settings', SimpleNamespace(PRODUCT_COOKIE_API_ENABLED=True, API_PREFIX='/api'))
    lookup = AsyncMock(return_value=SimpleNamespace(id=7))
    monkeypatch.setattr(product_session, 'get_session_user', lookup)
    request = Request({'type':'http','method':'GET','path':path,'headers':[(b'cookie',f'{COOKIE}=opaque'.encode())],'scheme':'https','server':('api.test',443),'query_string':b''})
    user = await auth.get_current_user(credentials=None, user_repo=object(), request=request)
    assert user.id == 7
    lookup.assert_awaited_once()
