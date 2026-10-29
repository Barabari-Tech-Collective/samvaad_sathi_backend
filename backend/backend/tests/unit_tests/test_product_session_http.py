from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException

from src.api.routes import product_session as routes
from src.services.product_session import Session


@pytest.fixture
async def setup(monkeypatch):
    config = SimpleNamespace(PRODUCT_SESSION_ENABLED=True, SSO_V2_ENABLED=True,
        SSO_TRANSACTION_ENABLED=True, CENTRAL_IDENTITY_CHECK_ENABLED=True,
        COGNITO_POST_LOGIN_REDIRECT_URL="https://student.test/auth/signup",
        SSO_REDIRECT_URI="https://api.test/auth/sso/callback",
        PRODUCT_SESSION_ENCRYPTION_KEYS=Fernet.generate_key().decode())
    store = SimpleNamespace(read=AsyncMock(return_value=None), revoke=AsyncMock(),
        create=AsyncMock(return_value=("n"*43, Session("u", "SECRET_ACCESS", "SECRET_REFRESH", "csrf-new"))))
    monkeypatch.setattr(routes, "settings", config)
    monkeypatch.setattr(routes.async_redis, "client", SimpleNamespace(get=AsyncMock(return_value=None),set=AsyncMock()))
    monkeypatch.setattr(routes, "ProductSessions", lambda *args: store)
    monkeypatch.setattr(routes, "verified_principal", AsyncMock(return_value="u"))
    monkeypatch.setattr(routes.sso_handoff, "redeem", AsyncMock(return_value={"token":"SECRET_ACCESS", "refresh_token":"SECRET_REFRESH", "return_to":"/practice"}))
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.test",
        headers={"origin":"https://student.test"}) as client:
        yield client, config, store


@pytest.mark.asyncio
async def test_completion_returns_metadata_only_with_host_cookie(setup):
    client, _, store = setup
    response = await client.post('/auth/session/complete', json={"ticket":"t"*43})
    assert response.status_code == 200
    assert response.json() == {"authenticated":True,"csrf_token":"csrf-new","return_to":"/practice"}
    assert 'SECRET' not in response.text
    cookie = response.headers.get_list('set-cookie')[0]
    assert '__Host-samvaad_session=' in cookie
    assert all(value in cookie for value in ['HttpOnly','Secure','SameSite=lax','Path=/'])
    assert 'Domain=' not in cookie
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('path,method',[('/auth/session','GET'),('/auth/session/logout','POST'),('/auth/session/complete','POST')])
@pytest.mark.parametrize('origin',['https://evil.test','null','https://student.test.evil.test'])
async def test_bad_origins_do_not_touch_session_store(setup,path,method,origin):
    client, _, store = setup
    response = await client.request(method,path,headers={'origin':origin},json={'ticket':'t'*43})
    assert response.status_code == 403
    store.read.assert_not_awaited()
    assert response.headers['cache-control']=='no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('flag',['PRODUCT_SESSION_ENABLED','SSO_V2_ENABLED','SSO_TRANSACTION_ENABLED','CENTRAL_IDENTITY_CHECK_ENABLED'])
async def test_all_rollout_guards_required(setup,flag):
    client, config, store = setup
    setattr(config,flag,False)
    assert (await client.get('/auth/session')).status_code==503
    store.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_http_configuration_is_rejected(setup):
    client, config, store=setup
    config.SSO_REDIRECT_URI='http://api.test/callback'
    assert (await client.get('/auth/session')).status_code==503
    store.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_rechecks_identity_and_hides_tokens(setup):
    client, _, store=setup
    store.read.return_value=Session('u','SECRET_ACCESS','SECRET_REFRESH','csrf')
    response=await client.get('/auth/session')
    assert response.json()=={'authenticated':True,'principal':'u','csrf_token':'csrf'}
    routes.verified_principal.assert_awaited_once_with('SECRET_ACCESS')


@pytest.mark.asyncio
async def test_status_identity_mismatch_revokes(setup):
    client, _, store=setup
    store.read.return_value=Session('other','a','r','csrf')
    assert (await client.get('/auth/session')).status_code==401
    store.revoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_access_not_reported_as_authenticated(setup):
    client, _, store=setup
    store.read.return_value=Session('u','a','r','csrf')
    routes.verified_principal.side_effect=HTTPException(403,'revoked')
    assert (await client.get('/auth/session')).status_code==403


@pytest.mark.asyncio
@pytest.mark.parametrize('path',['/auth/session/complete','/auth/session/logout'])
async def test_stale_csrf_cannot_switch_or_logout_account(setup,path):
    client, _, store=setup
    store.read.return_value=Session('u','a','r','new-account-csrf')
    response=await client.post(path,headers={'x-csrf-token':'old-account-csrf'},json={'ticket':'t'*43})
    assert response.status_code==403
    store.revoke.assert_not_awaited()
    routes.sso_handoff.redeem.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_works_when_token_expired_without_central_call(setup):
    client, _, store=setup
    store.read.return_value=Session('u','expired','r','csrf')
    response=await client.post('/auth/session/logout',headers={'x-csrf-token':'csrf'})
    assert response.status_code==200
    assert 'Max-Age=0' in response.headers['set-cookie']
    routes.verified_principal.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_missing_session_is_idempotent(setup):
    client, _, _=setup
    assert (await client.post('/auth/session/logout')).status_code==200
    assert (await client.post('/auth/session/logout')).status_code==200


@pytest.mark.asyncio
async def test_replay_handoff_cannot_allocate_session(setup):
    client, _, store=setup
    routes.sso_handoff.redeem.return_value=None
    assert (await client.post('/auth/session/complete',json={'ticket':'t'*43})).status_code==401
    store.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_old_session_revocation_cleans_new_session_without_cookie(setup):
    client, _, store=setup
    store.revoke.side_effect=[ConnectionError('SECRET error'),None]
    response=await client.post('/auth/session/complete',json={'ticket':'t'*43})
    assert response.status_code==503 and 'SECRET' not in response.text
    assert 'set-cookie' not in response.headers
    assert store.revoke.await_args_list[-1].args==('n'*43,)


@pytest.mark.asyncio
async def test_store_outage_does_not_clear_browser_cookie(setup):
    client, _, store=setup
    store.read.side_effect=ConnectionError('SECRET')
    response=await client.post('/auth/session/logout')
    assert response.status_code==503 and 'set-cookie' not in response.headers
    assert 'SECRET' not in response.text


@pytest.mark.asyncio
async def test_expired_status_retains_logout_csrf_without_identity(setup):
    client, _, store=setup
    store.read.return_value=Session('u','expired','r','csrf')
    routes.verified_principal.side_effect=HTTPException(401,'expired')
    response=await client.get('/auth/session')
    assert response.status_code==401
    assert response.json()=={'authenticated':False,'csrf_token':'csrf','detail':'expired'}


@pytest.mark.asyncio
async def test_validation_errors_never_echo_ticket_or_cache(setup):
    client, _, _=setup
    response=await client.post('/auth/session/complete',json={'ticket':'SECRET-invalid-ticket'})
    assert response.status_code==422 and 'SECRET' not in response.text
    assert response.headers['cache-control']=='no-store'


@pytest.mark.asyncio
async def test_actual_redis_http_completion_status_logout(monkeypatch):
    import os
    if os.getenv('RUN_LOCAL_AUTH_REDIS')!='1':
        pytest.skip('Explicit isolated Redis acceptance only')
    import redis.asyncio as redis
    from src.services.product_session import ProductSessions
    from src.services import sso_handoff
    redis_client=redis.Redis(host='127.0.0.1',port=16379,db=15,socket_connect_timeout=2,socket_timeout=2)
    config=SimpleNamespace(PRODUCT_SESSION_ENABLED=True,SSO_V2_ENABLED=True,SSO_TRANSACTION_ENABLED=True,
        CENTRAL_IDENTITY_CHECK_ENABLED=True,COGNITO_POST_LOGIN_REDIRECT_URL='https://student.test/auth/signup',
        SSO_REDIRECT_URI='https://api.test/auth/sso/callback',PRODUCT_SESSION_ENCRYPTION_KEYS=Fernet.generate_key().decode())
    monkeypatch.setattr(routes,'settings',config)
    monkeypatch.setattr(routes,'async_redis',SimpleNamespace(client=redis_client))
    monkeypatch.setattr(routes,'verified_principal',AsyncMock(return_value='synthetic-user'))
    store=ProductSessions(redis_client,[config.PRODUCT_SESSION_ENCRYPTION_KEYS])
    ticket,binding=await sso_handoff.issue(redis_client,'synthetic-access','synthetic-refresh','/practice')
    app=FastAPI();app.include_router(routes.router)
    sid=None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://api.test',headers={'origin':'https://student.test'}) as client:
            client.cookies.set(sso_handoff.COOKIE+ticket,binding,domain='api.test',path='/')
            result=await client.post('/auth/session/complete',json={'ticket':ticket})
            assert result.status_code==200
            assert 'synthetic-access' not in result.text and 'synthetic-refresh' not in result.text
            sid=client.cookies.get(routes.COOKIE)
            assert await store.read(sid) is not None
            assert (await client.get('/auth/session')).json()['principal']=='synthetic-user'
            csrf=result.json()['csrf_token']
            assert (await client.post('/auth/session/logout',headers={'x-csrf-token':'stale'})).status_code==403
            assert await store.read(sid) is not None
            lease=await store.reserve_refresh(sid)
            assert (await client.post('/auth/session/logout',headers={'x-csrf-token':csrf})).status_code==200
            assert not await store.finish_refresh(lease,'synthetic-user','late','late')
            assert (await client.get('/auth/session')).status_code==401
            assert (await client.post('/auth/session/complete',json={'ticket':ticket})).status_code==401
    finally:
        if sid: await store.revoke(sid)
        await redis_client.delete(sso_handoff.PREFIX+ticket)
        await redis_client.aclose()


@pytest.mark.asyncio
async def test_principal_verification_requires_central_signature(monkeypatch):
    def invalid(token): raise routes.SsoTokenError('invalid')
    monkeypatch.setattr(routes,'decode_sso_access_token',invalid)
    current=AsyncMock()
    monkeypatch.setattr(routes,'current_identity',current)
    with pytest.raises(HTTPException) as exc:
        await routes.verified_principal('untrusted')
    assert exc.value.status_code==401
    current.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('principal',[None,'','  ',42])
async def test_principal_must_be_stable_central_id(monkeypatch,principal):
    monkeypatch.setattr(routes,'decode_sso_access_token',lambda token:{})
    monkeypatch.setattr(routes,'current_identity',AsyncMock(return_value={'userUniqueId':principal}))
    with pytest.raises(HTTPException) as exc:
        await routes.verified_principal('verified')
    assert exc.value.status_code==401


@pytest.mark.asyncio
async def test_principal_rechecks_revocation(monkeypatch):
    monkeypatch.setattr(routes,'decode_sso_access_token',lambda token:{'userUniqueId':'u'})
    fresh=AsyncMock(return_value={'userUniqueId':'u'})
    revoked=AsyncMock(return_value=True)
    monkeypatch.setattr(routes,'current_identity',fresh)
    monkeypatch.setattr(routes,'is_access_revoked',revoked)
    with pytest.raises(HTTPException) as exc:
        await routes.verified_principal('verified')
    assert exc.value.status_code==403
    fresh.assert_awaited_once_with({'userUniqueId':'u'},token='verified')
    revoked.assert_awaited_once_with('u')


@pytest.mark.asyncio
async def test_refresh_route_enforces_csrf_and_returns_no_tokens(setup,monkeypatch):
    from src.services import product_session_refresh
    client, _, store=setup
    store.read.return_value=Session('u','expired','secret-refresh','csrf')
    refresh=AsyncMock()
    monkeypatch.setattr(product_session_refresh,'refresh',refresh)
    denied=await client.post('/auth/session/refresh')
    assert denied.status_code==403
    refresh.assert_not_awaited()
    allowed=await client.post('/auth/session/refresh',headers={'x-csrf-token':'csrf'})
    assert allowed.json()=={'refreshed':True}
    assert allowed.headers['cache-control']=='no-store'
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_route_returns_busy_without_internal_details(setup,monkeypatch):
    from src.services import product_session_refresh
    client, _, store=setup
    store.read.return_value=Session('u','expired','secret-refresh','csrf')
    monkeypatch.setattr(product_session_refresh,'refresh',AsyncMock(side_effect=HTTPException(409,'Refresh busy')))
    assert (await client.post('/auth/session/refresh',headers={'x-csrf-token':'csrf'})).status_code==409


@pytest.mark.asyncio
async def test_global_logout_waits_for_central_ack(setup,monkeypatch):
    from src.services import central_session_logout
    client,_,store=setup
    store.read.return_value=Session('u','SECRET_ACCESS','SECRET_REFRESH','csrf')
    central=AsyncMock()
    monkeypatch.setattr(central_session_logout,'revoke_everywhere',central)
    result=await client.post('/auth/session/logout-all',headers={'x-csrf-token':'csrf'})
    assert result.status_code==200
    assert result.json()['globally_revoked'] is True
    central.assert_awaited_once_with('SECRET_ACCESS')
    store.revoke.assert_awaited_once()
    assert 'SECRET' not in result.text


@pytest.mark.asyncio
async def test_failed_global_logout_preserves_local_session_for_retry(setup,monkeypatch):
    from src.services import central_session_logout
    client,_,store=setup
    store.read.return_value=Session('u','SECRET_ACCESS','SECRET_REFRESH','csrf')
    monkeypatch.setattr(central_session_logout,'revoke_everywhere',AsyncMock(side_effect=HTTPException(503,'Unavailable')))
    result=await client.post('/auth/session/logout-all',headers={'x-csrf-token':'csrf'})
    assert result.status_code==503
    store.revoke.assert_not_awaited()
    assert 'set-cookie' not in result.headers


@pytest.mark.asyncio
async def test_global_logout_rejects_stale_csrf(setup,monkeypatch):
    from src.services import central_session_logout
    client,_,store=setup
    store.read.return_value=Session('u','SECRET_ACCESS','SECRET_REFRESH','csrf')
    central=AsyncMock()
    monkeypatch.setattr(central_session_logout,'revoke_everywhere',central)
    result=await client.post('/auth/session/logout-all',headers={'x-csrf-token':'other'})
    assert result.status_code==403
    central.assert_not_awaited()
    store.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_logout_receipt_confirms_lost_response(setup,monkeypatch):
    from src.services import central_session_logout
    client,_,store=setup
    client.cookies.set(routes.COOKIE,'s'*43)
    store.read.return_value=Session('u','SECRET_ACCESS','SECRET_REFRESH','csrf')
    central=AsyncMock()
    monkeypatch.setattr(central_session_logout,'revoke_everywhere',central)
    first=await client.post('/auth/session/logout-all',headers={'x-csrf-token':'csrf'})
    assert first.status_code==200
    # Simulate browser not receiving the first response/cookie deletion.
    client.cookies.set(routes.COOKIE,'s'*43)
    routes.async_redis.client.get.return_value='confirmed'
    store.read.return_value=None
    second=await client.post('/auth/session/logout-all',headers={'x-csrf-token':'csrf'})
    assert second.status_code==200 and second.json()['globally_revoked'] is True
    central.assert_awaited_once()
