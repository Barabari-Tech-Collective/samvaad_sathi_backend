"""Explicit server-side refresh. Never retries an ambiguously consumed refresh token."""
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from src.config.manager import settings
from src.securities.authorizations.sso_jwt import decode_sso_access_token, SsoExpiredToken, SsoTokenError


async def exchange(refresh_token):
    base = settings.AUTH_SERVICE_BASE_URL.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(503, 'Secure refresh service is not configured.')
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        response = await client.post(base + '/barabari-auth/api/auth/public/v1/student-refresh-token',
                                     json={'refreshToken': refresh_token})
    if response.status_code != 200:
        raise HTTPException(401, 'Sign in again.')
    body = response.json()
    data = body.get('data') if isinstance(body, dict) and body.get('isSuccess') is True else None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k].strip() for k in ('accessToken','refreshToken')):
        raise ValueError('Invalid refresh envelope')
    return data['accessToken'], data['refreshToken']


async def refresh(store, sid, verify):
    lease = await store.reserve_refresh(sid)
    if lease is None:
        raise HTTPException(409, 'Session refresh is busy or expired. Check session status.')
    dispatched = False
    try:
        # Another request may have refreshed between the original 401 and this lease.
        try:
            decode_sso_access_token(lease.session.access_token)
            expired = False
        except SsoExpiredToken:
            expired = True
        except SsoTokenError:
            await store.revoke_refresh_snapshot(lease)
            raise HTTPException(401, 'Sign in again.') from None
        if not expired:
            if await verify(lease.session.access_token) != lease.session.principal:
                await store.revoke_refresh_snapshot(lease)
                raise HTTPException(401, 'Session identity changed.')
            return
        if not await store.begin_refresh(lease):
            await store.revoke_refresh_snapshot(lease)
            raise HTTPException(401, 'Previous refresh could not be confirmed. Sign in again.')
        dispatched = True
        access, refresh_token = await exchange(lease.session.refresh_token)
        principal = await verify(access)
        if not await store.finish_refresh(lease, principal, access, refresh_token):
            await store.revoke_refresh_snapshot(lease)
            raise HTTPException(401, 'Session changed during refresh. Sign in again.')
    except Exception:
        if dispatched:
            # Timeout, malformed reply, or identity outage after dispatch may mean rotation occurred.
            # Revocation (or the durable marker if Redis is down) prevents blind retries.
            await store.revoke_refresh_snapshot(lease)
        raise
    finally:
        await store.release_refresh(lease)
