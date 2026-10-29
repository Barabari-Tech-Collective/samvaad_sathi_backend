import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from starlette.responses import JSONResponse
from src.api.routes import product_session as routes
from src.services.product_session import ProductSessions, Session
from src.services import sso_handoff


@pytest.fixture
async def setup(monkeypatch):
    config=SimpleNamespace(PRODUCT_SESSION_ENABLED=True,SSO_V2_ENABLED=True,SSO_TRANSACTION_ENABLED=True,
        CENTRAL_IDENTITY_CHECK_ENABLED=True,COGNITO_POST_LOGIN_REDIRECT_URL='https://student.test/auth/signup',
        SSO_REDIRECT_URI='https://api.test/callback',PRODUCT_SESSION_ENCRYPTION_KEYS=Fernet.generate_key().decode())
    store=SimpleNamespace(read=AsyncMock(return_value=None),revoke=AsyncMock(),create=AsyncMock(return_value=('n'*43,Session('u','a','r','csrf'))))
    monkeypatch.setattr(routes,'settings',config)
    monkeypatch.setattr(routes,'ProductSessions',lambda *args:store)
    monkeypatch.setattr(routes,'verified_principal',AsyncMock(return_value='u'))
    monkeypatch.setattr(routes.sso_handoff,'redeem',AsyncMock(return_value={'token':'a','refresh_token':'r','return_to':'https://evil.test'}))
    app=FastAPI();app.include_router(routes.router)
    extra=routes.APIRouter(route_class=routes.SessionRoute)
    @extra.get('/failure')
    async def failure(): raise RuntimeError('sensitive-database-detail')
    @extra.get('/vary')
    async def vary(): return JSONResponse({},headers={'Vary':'Accept-Encoding'})
    app.include_router(extra)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://api.test',headers={'origin':'https://student.test'}) as client:
        yield client,config,store


@pytest.mark.asyncio
@pytest.mark.parametrize('url',[
    'https://student.test/\ncallback','https://student.test/ callback',
    'https://student.test:bad/callback','https://student.test:99999/callback','https://student.test:0/callback',
    'https://student.test/callback?secret=x','https://student.test/callback#fragment',
    'https://user:password@student.test/callback','https://student.test\\evil/callback'])
async def test_unsafe_config_fails_before_storage(setup,url):
    client,config,store=setup;config.COGNITO_POST_LOGIN_REDIRECT_URL=url
    response=await client.get('/auth/session')
    assert response.status_code==503
    store.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_origin_is_not_parser_dependent(setup):
    client,_,store=setup
    response=await client.get('/auth/session',headers=[('origin','https://student.test'),('origin','https://student.test')])
    assert response.status_code==403;store.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_site_metadata_rejected(setup):
    client,_,store=setup
    response=await client.get('/auth/session',headers={'sec-fetch-site':'cross-site'})
    assert response.status_code==403;store.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('name',[routes.COOKIE,sso_handoff.COOKIE+'t'*43])
async def test_duplicate_sensitive_cookie_rejected(setup,name):
    client,_,store=setup
    response=await client.get('/auth/session',headers={'cookie':f'{name}=one; {name}=two'})
    assert response.status_code==400;store.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_csrf_cannot_logout(setup):
    client,_,store=setup;store.read.return_value=Session('u','a','r','csrf')
    response=await client.post('/auth/session/logout',headers=[('x-csrf-token','csrf'),('x-csrf-token','csrf')])
    assert response.status_code==403;store.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_csrf_cannot_logout(setup):
    client,_,store=setup;store.read.return_value=Session('u','a','r','x'*129)
    response=await client.post('/auth/session/logout',headers={'x-csrf-token':'x'*129})
    assert response.status_code==403;store.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_forbids_client_identity_and_token_fields(setup):
    client,_,store=setup
    response=await client.post('/auth/session/complete',json={'ticket':'t'*43,'principal':'admin','token':'SECRET'})
    assert response.status_code==422 and 'SECRET' not in response.text
    store.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_sanitizes_stored_destination(setup):
    client,_,_=setup
    response=await client.post('/auth/session/complete',json={'ticket':'t'*43})
    assert response.status_code==200 and response.json()['return_to']=='/'


@pytest.mark.asyncio
async def test_unexpected_endpoint_error_is_private(setup):
    client,_,_=setup
    response=await client.get('/failure')
    assert response.status_code==503 and 'sensitive' not in response.text
    assert response.headers['cache-control']=='no-store'
    assert response.headers['referrer-policy']=='no-referrer'


@pytest.mark.asyncio
async def test_vary_preserves_existing_dimensions_and_cookies(setup):
    client,_,_=setup
    response=await client.get('/vary')
    assert set(response.headers['vary'].split(', '))=={'Accept-Encoding','Origin','Cookie'}
    assert response.headers['x-content-type-options']=='nosniff'


def test_keyring_rejects_duplicates_and_unbounded_history():
    key=Fernet.generate_key()
    for keys in ([key,key],[Fernet.generate_key() for _ in range(4)],key):
        with pytest.raises(ValueError): ProductSessions(AsyncMock(),keys)


@pytest.mark.parametrize('raw',[b'x'*100001,123,[],{}])
def test_oversized_and_wrong_type_records_fail_closed(raw):
    store=ProductSessions(AsyncMock(),[Fernet.generate_key()])
    assert store.decode(raw) is None


def test_encrypted_record_rejects_extra_fields():
    store=ProductSessions(AsyncMock(),[Fernet.generate_key()])
    data={'principal':'u','access_token':'a','refresh_token':'r','csrf':'c','generation':0,'admin':True}
    assert store.decode(store.cipher.encrypt(json.dumps(data).encode())) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('values',[('u'*129,'a','r'),(' u','a','r'),('u','a'*16385,'r'),('u','a','r'*16385)])
async def test_session_create_field_limits_precede_redis(values):
    client=AsyncMock();store=ProductSessions(client,[Fernet.generate_key()])
    with pytest.raises(ValueError): await store.create(*values)
    client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_issue_rejects_empty_credentials_and_normalizes_redirect():
    client=AsyncMock()
    with pytest.raises(ValueError): await sso_handoff.issue(client,' ','r','/')
    client.set.assert_not_awaited()
    await sso_handoff.issue(client,'a','r','https://evil.test')
    payload=json.loads(client.set.call_args.args[1].split(':',1)[1])
    assert payload['return_to']=='/'


@pytest.mark.asyncio
async def test_handoff_redeem_strips_unexpected_fields():
    client=AsyncMock();client.eval.return_value=json.dumps({'token':'a','refresh_token':'r','return_to':'//evil.test','admin':True})
    result=await sso_handoff.redeem(client,'t'*43,'b'*43)
    assert result=={'token':'a','refresh_token':'r','return_to':'/'}


@pytest.mark.parametrize('field,value', [('principal',' u'),('principal','u'*129),('access_token','a'*16385),('refresh_token','r'*16385),('csrf','c'*129)])
def test_encrypted_record_field_limits(field,value):
    store=ProductSessions(AsyncMock(),[Fernet.generate_key()])
    data={'principal':'u','access_token':'a','refresh_token':'r','csrf':'c','generation':0}
    data[field]=value
    assert store.decode(store.cipher.encrypt(json.dumps(data).encode())) is None


@pytest.mark.asyncio
async def test_oversized_refreshed_token_cannot_commit():
    from src.services.product_session import RefreshLease
    client=AsyncMock();store=ProductSessions(client,[Fernet.generate_key()])
    lease=RefreshLease('s'*43,'owner',b'old',Session('u','a','r','c'))
    with pytest.raises(ValueError): await store.finish_refresh(lease,'u','a'*16385,'r')
    client.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_redeem_rejects_whitespace_credentials():
    client=AsyncMock();client.eval.return_value=json.dumps({'token':' ','refresh_token':'r','return_to':'/'})
    assert await sso_handoff.redeem(client,'t'*43,'b'*43) is None
