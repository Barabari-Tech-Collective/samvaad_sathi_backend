from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from src.services import product_session_refresh as service
from src.services.product_session import Session, RefreshLease


@pytest.fixture
def setup(monkeypatch):
    lease=RefreshLease('s'*43,'owner',b'cipher',Session('u','expired','refresh','csrf'))
    store=SimpleNamespace(reserve_refresh=AsyncMock(return_value=lease),begin_refresh=AsyncMock(return_value=True),
        finish_refresh=AsyncMock(return_value=True),revoke=AsyncMock(),revoke_refresh_snapshot=AsyncMock(),release_refresh=AsyncMock())
    def expired(token): raise service.SsoExpiredToken('expired')
    monkeypatch.setattr(service,'decode_sso_access_token',expired)
    monkeypatch.setattr(service,'exchange',AsyncMock(return_value=('new-access','new-refresh')))
    verify=AsyncMock(return_value='u')
    return store,verify


@pytest.mark.asyncio
async def test_refresh_validates_and_commits_without_exposing_tokens(setup):
    store,verify=setup
    assert await service.refresh(store,'s'*43,verify) is None
    verify.assert_awaited_once_with('new-access')
    store.finish_refresh.assert_awaited_once()
    store.revoke_refresh_snapshot.assert_not_awaited()
    store.release_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_refresh_never_calls_provider(setup):
    store,verify=setup;store.reserve_refresh.return_value=None
    with pytest.raises(HTTPException) as exc: await service.refresh(store,'s'*43,verify)
    assert exc.value.status_code==409
    service.exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_token_rechecks_identity_without_rotation(setup,monkeypatch):
    store,verify=setup
    monkeypatch.setattr(service,'decode_sso_access_token',lambda t:{})
    await service.refresh(store,'s'*43,verify)
    verify.assert_awaited_once_with('expired')
    service.exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_signature_never_uses_refresh_token(setup,monkeypatch):
    store,verify=setup
    def invalid(t): raise service.SsoTokenError('invalid')
    monkeypatch.setattr(service,'decode_sso_access_token',invalid)
    with pytest.raises(HTTPException): await service.refresh(store,'s'*43,verify)
    store.revoke_refresh_snapshot.assert_awaited_once()
    service.exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_previous_dispatch_blocks_reuse(setup):
    store,verify=setup;store.begin_refresh.return_value=False
    with pytest.raises(HTTPException): await service.refresh(store,'s'*43,verify)
    service.exchange.assert_not_awaited()
    store.revoke_refresh_snapshot.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[TimeoutError(),ValueError(),HTTPException(401,'invalid')])
async def test_ambiguous_or_failed_exchange_revokes_without_retry(setup,error):
    store,verify=setup;service.exchange.side_effect=error
    with pytest.raises(type(error)): await service.refresh(store,'s'*43,verify)
    assert service.exchange.await_count==1
    store.revoke_refresh_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_identity_outage_after_rotation_revokes(setup):
    store,verify=setup;verify.side_effect=HTTPException(503,'unavailable')
    with pytest.raises(HTTPException): await service.refresh(store,'s'*43,verify)
    store.revoke_refresh_snapshot.assert_awaited_once()
    store.finish_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_during_exchange_cannot_return_success(setup):
    store,verify=setup;store.finish_refresh.return_value=False
    with pytest.raises(HTTPException) as exc: await service.refresh(store,'s'*43,verify)
    assert exc.value.status_code==401
    store.revoke_refresh_snapshot.assert_awaited()


@pytest.mark.asyncio
async def test_cookie_dependency_preserves_existing_user_checks(monkeypatch):
    from src.api.dependencies import product_session as dep
    from starlette.requests import Request
    store=SimpleNamespace(read=AsyncMock(return_value=Session('u','access','refresh','csrf')),revoke=AsyncMock())
    monkeypatch.setattr(dep,'boundary',lambda r:store)
    monkeypatch.setattr(dep,'verified_principal',AsyncMock(return_value='u'))
    user=object();lookup=AsyncMock(return_value=user)
    monkeypatch.setattr(dep,'get_current_user',lookup)
    req=Request({'type':'http','method':'POST','headers':[(b'x-csrf-token',b'csrf')]})
    assert await dep.get_session_user(req,user_repo='repo') is user
    assert lookup.call_args.kwargs['credentials'].credentials=='access'
    assert lookup.call_args.kwargs['user_repo']=='repo'


@pytest.mark.asyncio
async def test_cookie_dependency_rejects_stale_csrf_before_auth(monkeypatch):
    from src.api.dependencies import product_session as dep
    from starlette.requests import Request
    store=SimpleNamespace(read=AsyncMock(return_value=Session('u','access','refresh','csrf')))
    monkeypatch.setattr(dep,'boundary',lambda r:store)
    verify=AsyncMock();monkeypatch.setattr(dep,'verified_principal',verify)
    with pytest.raises(HTTPException) as exc:
        await dep.get_session_user(Request({'type':'http','method':'POST','headers':[]}),user_repo='repo')
    assert exc.value.status_code==403
    verify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('body,status',[
    ({'isSuccess':True,'data':{'accessToken':'a','refreshToken':'r'}},200),
    ({'isSuccess':True,'data':{}},200),([],200),({'isSuccess':False},200),
    ({'isSuccess':True,'data':{'accessToken':'a','refreshToken':'r'}},302),
    ({},401),({},500)])
async def test_refresh_transport_validates_envelope_without_redirects(monkeypatch,body,status):
    import httpx
    original=httpx.AsyncClient
    seen=[]
    async def handler(request):
        seen.append(request)
        return httpx.Response(status,json=body,headers={'Location':'https://evil.test/'})
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(service,'settings',SimpleNamespace(AUTH_SERVICE_BASE_URL='https://central.test'))
    monkeypatch.setattr(service.httpx,'AsyncClient',client)
    good=status==200 and isinstance(body,dict) and body.get('data',{}).get('accessToken')=='a'
    if good:
        assert await service.exchange('secret-refresh')==('a','r')
    else:
        with pytest.raises((HTTPException,ValueError)): await service.exchange('secret-refresh')
    assert len(seen)==1 and seen[0].url.host=='central.test'
    assert 'secret-refresh' not in str(seen[0].url)


@pytest.mark.asyncio
async def test_unsigned_expired_jwt_is_not_refreshable():
    import time
    from jose import jwt
    from src.securities.authorizations.sso_jwt import decode_sso_access_token,SsoTokenError,SsoExpiredToken
    token=jwt.encode({'sub':'x','exp':int(time.time())-10,'role':'STUDENT'},'wrong-key',algorithm='HS256')
    with pytest.raises(SsoTokenError) as exc: decode_sso_access_token(token)
    assert not isinstance(exc.value,SsoExpiredToken)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['lease_lost','exchange_timeout','commit_lost','invalid_signature','identity_mismatch'])
async def test_refresh_failure_never_uses_unconditional_revocation(setup,monkeypatch,failure):
    store,verify=setup
    if failure=='lease_lost': store.begin_refresh.return_value=False
    elif failure=='exchange_timeout': service.exchange.side_effect=TimeoutError()
    elif failure=='commit_lost': store.finish_refresh.return_value=False
    elif failure=='invalid_signature':
        def invalid(token): raise service.SsoTokenError('bad')
        monkeypatch.setattr(service,'decode_sso_access_token',invalid)
    elif failure=='identity_mismatch':
        monkeypatch.setattr(service,'decode_sso_access_token',lambda token:{})
        verify.return_value='other'
    with pytest.raises((HTTPException,TimeoutError)):
        await service.refresh(store,'s'*43,verify)
    store.revoke.assert_not_awaited()
    assert store.revoke_refresh_snapshot.await_count >= 1
    for call in store.revoke_refresh_snapshot.await_args_list:
        assert call.args==(store.reserve_refresh.return_value,)
