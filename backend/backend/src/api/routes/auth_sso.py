import secrets
from urllib.parse import quote, urlencode

import fastapi
import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse

from src.config.manager import settings
from src.api.dependencies.repository import get_repository
from src.repository.crud.user import UserCRUDRepository
from src.utilities.exceptions.database import EntityDoesNotExist
from src.securities.authorizations.sso_jwt import decode_sso_access_token, SsoTokenError

router = fastapi.APIRouter(prefix="/auth/sso", tags=["users"])


@router.get("/login", name="auth_sso_login")
async def sso_login(request: Request):
    """
    Replaces /auth/cognito/login as the entry point a frontend button redirects to.
    Sends the browser to auth-service's /authorize, which either silently recognizes an
    existing cross-product session (shared barabari_sso cookie) or bounces to the central
    Sampark Saathi login page first.
    """
    state = secrets.token_urlsafe(24)
    # Mirrors how auth_cognito.py used the session for CSRF-adjacent bookkeeping (authlib
    # owns the OAuth `state` there; here we own it ourselves since we're not using authlib).
    request.session["sso_state"] = state

    # auth-service matches redirect_uri by exact string against its registered
    # allow-list, so prefer the explicitly-configured value. Deriving it from the
    # request is unsafe behind a TLS-terminating proxy: url_for() reads the request
    # scheme, which is http unless uvicorn trusts X-Forwarded-Proto, and the resulting
    # http:// URL silently fails to match the registered https:// entry.
    redirect_uri = settings.SSO_REDIRECT_URI or str(request.url_for("auth_sso_callback"))
    authorize_url = f"{settings.AUTH_SERVICE_BASE_URL}/barabari-auth/api/auth/public/v1/authorize?" + urlencode(
        {
            "product": settings.SAMPARK_PRODUCT_UNIQUE_ID,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return RedirectResponse(url=authorize_url)


@router.get("/callback", name="auth_sso_callback")
async def sso_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
):
    """
    auth-service redirects here (this URL is exactly what /login above sent as
    redirect_uri) with a one-time `code`. Exchanges it server-to-server for a token pair,
    upserts the local User row by email, and hands the browser back to the frontend the
    same way auth_cognito.py's /authorize did: token/refresh_token in the query string.
    """
    target = settings.COGNITO_POST_LOGIN_REDIRECT_URL or "/"
    expected_state = request.session.pop("sso_state", None)

    if not state or not expected_state or state != expected_state:
        return RedirectResponse(url=f"{target}#error={quote('Invalid or missing state')}")
    if not code:
        return RedirectResponse(url=f"{target}#error={quote('Missing authorization code')}")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{settings.AUTH_SERVICE_BASE_URL}/barabari-auth/api/auth/public/v1/token",
                json={"code": code},
            )
        except httpx.HTTPError as exc:
            return RedirectResponse(url=f"{target}#error={quote(f'Sampark Saathi unreachable: {exc}')}")

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200 or not body.get("isSuccess"):
        message = body.get("errorMessage") or "Sign-in failed"
        return RedirectResponse(url=f"{target}#error={quote(message)}")

    access_token = body["data"]["accessToken"]
    refresh_token = body["data"]["refreshToken"]

    try:
        claims = decode_sso_access_token(access_token)
    except SsoTokenError:
        return RedirectResponse(url=f"{target}#error={quote('Received an unverifiable token')}")

    email = claims.get("sub")
    if not email:
        return RedirectResponse(url=f"{target}#error={quote('Token is missing an email claim')}")

    try:
        await user_repo.get_user_by_email(email=email)
    except EntityDoesNotExist:
        # First time this student has reached Samvaad Saathi via Sampark Saathi SSO -
        # mirrors auth_cognito.py's authorize(), which likewise auto-created a local User
        # row keyed by email the first time a Cognito login came through. password_hash is
        # unused for SSO accounts (auth-service owns the credential), so a random value
        # keeps the NOT NULL column satisfied without a usable local password existing.
        random_password = secrets.token_urlsafe(16)
        name = email.split("@")[0]
        await user_repo.create_user(email=email, password=random_password, name=name)

    return RedirectResponse(url=f"{target}?token={quote(access_token)}&refresh_token={quote(refresh_token)}")


@router.post("/refresh")
async def sso_refresh(refresh_token: str = fastapi.Form(...)):
    """
    Same request/response shape as the legacy /auth/cognito/refresh (form-encoded
    refresh_token in, {token, refresh_token} out) so the frontend's existing 401-retry
    interceptor (src/lib/api-config/src/config.ts) needs only an endpoint URL swap, not a
    rewrite, when it cuts over. Delegates to auth-service, which now owns refresh-token
    validity (this service no longer has its own Session-table-backed refresh tokens for
    SSO accounts).
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{settings.AUTH_SERVICE_BASE_URL}/barabari-auth/api/auth/public/v1/student-refresh-token",
                json={"refreshToken": refresh_token},
            )
        except httpx.HTTPError as exc:
            raise fastapi.HTTPException(status_code=502, detail=f"Sampark Saathi unreachable: {exc}")

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200 or not body.get("isSuccess"):
        raise fastapi.HTTPException(status_code=401, detail=body.get("errorMessage") or "Invalid or expired refresh token")

    return {"token": body["data"]["accessToken"], "refresh_token": body["data"]["refreshToken"]}
