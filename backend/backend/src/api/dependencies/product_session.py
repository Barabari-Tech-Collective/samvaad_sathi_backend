"""Explicit opt-in cookie dependency; existing bearer routes are not silently changed."""
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from src.api.routes.product_session import boundary, check_csrf, COOKIE, verified_principal
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.repository.crud.user import UserCRUDRepository


async def get_session_user(request: Request, user_repo: UserCRUDRepository = Depends(get_repository(repo_type=UserCRUDRepository))):
    try:
        store = boundary(request)
        sid = request.cookies.get(COOKIE)
        session = await store.read(sid)
        if session is None:
            raise HTTPException(401, 'Sign in again.')
        expected = request.headers.get("x-session-principal")
        if expected is not None and (len(request.headers.getlist("x-session-principal")) != 1 or expected != session.principal):
            raise HTTPException(409, 'Your account changed. Reload this task.')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            check_csrf(request, session)
        if await verified_principal(session.access_token) != session.principal:
            await store.revoke(sid)
            raise HTTPException(401, 'Session identity changed.')
        # Reuse all current user binding, identity freshness, role and revocation checks.
        # Resource-specific permission dependencies remain the endpoint's responsibility.
        return await get_current_user(credentials=HTTPAuthorizationCredentials(scheme='Bearer', credentials=session.access_token), user_repo=user_repo)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Session service temporarily unavailable.') from None
