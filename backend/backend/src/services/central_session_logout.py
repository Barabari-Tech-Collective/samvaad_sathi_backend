"""Global revocation must be acknowledged centrally before local logout succeeds."""
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from src.config.manager import settings


async def revoke_everywhere(access_token: str, *, transport=None):
    base = settings.AUTH_SERVICE_BASE_URL.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(503, 'Secure central logout is not configured.')
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, transport=transport) as client:
            result = await client.post(base + '/barabari-auth/api/auth/public/v2/logout-all',
                                      headers={'Authorization': 'Bearer ' + access_token}, json={})
        if result.status_code == 401:
            raise HTTPException(401, 'Fresh authentication required for global logout.')
        if result.status_code != 200 or result.json().get('isSuccess') is not True:
            raise ValueError('Revocation not acknowledged')
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Global sign-out could not be confirmed. Please retry.') from None
