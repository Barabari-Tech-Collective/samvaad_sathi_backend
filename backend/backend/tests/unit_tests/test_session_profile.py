import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from src.api.routes import users
from src.api.dependencies import product_session as auth
from src.services.product_session import Session
from src.api.dependencies.session import get_async_session


@pytest.fixture
async def setup(monkeypatch):
    user=SimpleNamespace(id=7,email='student@example.org',name='Student',created_at=datetime.datetime.now(datetime.timezone.utc),
        is_onboarded=True,degree=None,university=None,target_position=None,years_experience=None,resume_text=None,skills={},company=None)
    permissions={'extra_claims':{},'is_admin':False,'is_super_admin':False,'access_role':'student','role':'STUDENT',
        'is_team_lead':False,'colleges':[],'domain':None,'purposes':[],'can_create_roles':False,'can_access_ats':False,
        'can_edit':False,'is_view_only':True,'team_name':None,'membership_roles':[]}
    monkeypatch.setattr(users,'get_user_active_memberships',AsyncMock(return_value=[]))
    monkeypatch.setattr(users,'_extract_primary_membership_and_claims',lambda *args:permissions)
    monkeypatch.setattr(users,'is_bulk_ats_user',lambda u:False)
    mint=Mock(return_value='legacy-token');monkeypatch.setattr(users.jwt_generator,'generate_access_token_for_user',mint)
    store=SimpleNamespace(read=AsyncMock(return_value=Session('central-u','access','refresh','csrf')),revoke=AsyncMock())
    monkeypatch.setattr(auth,'boundary',lambda req:store)
    monkeypatch.setattr(auth,'verified_principal',AsyncMock(return_value='central-u'))
    lookup=AsyncMock(return_value=user);monkeypatch.setattr(auth,'get_current_user',lookup)
    summary=SimpleNamespace(count_by_user=AsyncMock(return_value=3))
    db=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda:[])))
    app=FastAPI();app.include_router(users.router)
    app.dependency_overrides[get_async_session]=lambda:db
    # Override repositories, not the cookie authentication dependency.
    for route in app.routes:
        if getattr(route,'path',None)=='/auth/session/me':
            for dep in route.dependant.dependencies:
                if dep.name=='summary_repo': app.dependency_overrides[dep.call]=lambda:summary
                if dep.name=='current_user':
                    for child in dep.dependencies:
                        if child.name=='user_repo': app.dependency_overrides[child.call]=lambda:object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://api.test') as client:
        yield client,store,lookup,mint,user,summary,db,permissions


@pytest.mark.asyncio
async def test_cookie_profile_never_mints_or_returns_tokens(setup):
    client,store,lookup,mint,*_=setup
    response=await client.get('/auth/session/me')
    assert response.status_code==200
    body=response.json()
    assert body['userId']==7 and body['authorizedUser']['totalAttempts']==3
    assert 'token' not in body['authorizedUser'] and 'refreshToken' not in body['authorizedUser']
    mint.assert_not_called()
    assert lookup.call_args.kwargs['credentials'].credentials=='access'
    assert response.headers['cache-control']=='no-store'


@pytest.mark.asyncio
async def test_missing_cookie_session_cannot_fall_back_to_bearer(setup):
    client,store,lookup,mint,*_=setup;store.read.return_value=None
    response=await client.get('/auth/session/me',headers={'Authorization':'Bearer other-account'})
    assert response.status_code==401
    assert response.headers['cache-control']=='no-store'
    lookup.assert_not_awaited();mint.assert_not_called()


@pytest.mark.asyncio
async def test_existing_access_denial_propagates_without_profile(setup):
    client,store,lookup,mint,*_=setup;lookup.side_effect=HTTPException(403,'Access revoked')
    response=await client.get('/auth/session/me')
    assert response.status_code==403 and 'authorizedUser' not in response.json()
    assert response.headers['cache-control']=='no-store'
    mint.assert_not_called()


@pytest.mark.asyncio
async def test_me_profile_never_mints_a_local_token(setup):
    _,_,_,mint,user,summary,db,_=setup
    result=await users.get_me(current_user=user,summary_repo=summary,session=db)
    assert result.authorized_user.token is None  # /me never mints a local token
    assert result.authorized_user.refresh_token is None
    mint.assert_not_called()


@pytest.mark.asyncio
async def test_permission_metadata_is_preserved_without_granting_extra_access(setup):
    client,_,_,_,_,_,_,permissions=setup
    permissions.update(role='FACILITATOR',can_edit=True,membership_roles=['FACILITATOR'])
    profile=(await client.get('/auth/session/me')).json()['authorizedUser']
    assert profile['role']=='FACILITATOR' and profile['canEdit'] is True
    assert profile['isAdmin'] is False and profile['canCreateRoles'] is False


@pytest.mark.asyncio
async def test_storage_outage_returns_private_service_error(setup):
    client,store,*_=setup;store.read.side_effect=ConnectionError('secret connection')
    response=await client.get('/auth/session/me')
    assert response.status_code==503 and 'secret' not in response.text
    assert response.headers['cache-control']=='no-store'


@pytest.mark.asyncio
async def test_profile_flag_off_blocks_cookie_path(setup,monkeypatch):
    from src.api.routes import product_session as lifecycle
    client,_,lookup,*_=setup
    monkeypatch.setattr(auth,'boundary',lifecycle.boundary)
    monkeypatch.setattr(lifecycle,'settings',SimpleNamespace(PRODUCT_SESSION_ENABLED=False))
    response=await client.get('/auth/session/me')
    assert response.status_code==503 and response.headers['cache-control']=='no-store'
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_exact_origin_rejected_before_storage(setup,monkeypatch):
    from src.api.routes import product_session as lifecycle
    client,_,lookup,*_=setup
    monkeypatch.setattr(auth,'boundary',lifecycle.boundary)
    monkeypatch.setattr(lifecycle,'settings',SimpleNamespace(PRODUCT_SESSION_ENABLED=True,SSO_V2_ENABLED=True,
        SSO_TRANSACTION_ENABLED=True,CENTRAL_IDENTITY_CHECK_ENABLED=True,
        COGNITO_POST_LOGIN_REDIRECT_URL='https://student.test/auth/signup',SSO_REDIRECT_URI='https://api.test/callback'))
    response=await client.get('/auth/session/me',headers={'origin':'https://student.test.evil.test'})
    assert response.status_code==403 and response.headers['cache-control']=='no-store'
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_expected_principal_cannot_read_another_account(setup):
    client,_,lookup,*_=setup
    response=await client.get('/auth/session/me',headers={'x-session-principal':'old-account'})
    assert response.status_code==409
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_expected_principal_matches_cookie_identity(setup):
    client,_,lookup,*_=setup
    response=await client.get('/auth/session/me',headers={'x-session-principal':'central-u'})
    assert response.status_code==200
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_duplicate_expected_principal_rejected(setup):
    client,_,lookup,*_=setup
    response=await client.get('/auth/session/me',headers=[('x-session-principal','central-u'),('x-session-principal','central-u')])
    assert response.status_code==409
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_bff_profile_never_mints_legacy_credentials(setup):
    _,_,_,mint,user,summary,db,_=setup
    response=await users.get_credential_free_profile(current_user=user,summary_repo=summary,session=db)
    body=response.model_dump(by_alias=True)
    assert body['userId']==7
    assert 'token' not in body['authorizedUser']
    assert 'refreshToken' not in body['authorizedUser']
    mint.assert_not_called()
