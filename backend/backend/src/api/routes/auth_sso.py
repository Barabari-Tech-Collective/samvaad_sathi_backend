import secrets
from urllib.parse import quote, urlencode

import fastapi
import httpx
import pydantic
from fastapi import Request
from fastapi.responses import RedirectResponse

from src.config.manager import settings
from src.api.dependencies.repository import get_repository
from src.repository.crud.user import UserCRUDRepository
from src.utilities.exceptions.database import EntityDoesNotExist
from src.securities.authorizations.sso_jwt import decode_sso_access_token, SsoTokenError


class RequestAccessBody(pydantic.BaseModel):
    request_code: str

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
    error: str | None = None,
    requestCode: str | None = None,  # noqa: N803 - matches auth-service's query param name
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
    # auth-service's /authorize sends this instead of a code when the student is
    # authenticated but doesn't have Samvaad Saathi in their product entitlements
    # (central-auth Phase 6) - a real, expected outcome, not a protocol error, so it gets
    # its own message rather than falling through to the generic "missing code" one below.
    # requestCode (central-auth Phase 10) rides along in the same redirect: the frontend's
    # "Request access" button needs it to call /auth/sso/request-access below, since this
    # denied student has no bearer token at all to authenticate that call otherwise.
    if error == "access_denied":
        params = urlencode({"error": "access_denied", "requestCode": requestCode or ""})
        return RedirectResponse(url=f"{target}#{params}")
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
    central_user_id = claims.get("userUniqueId")
    if not email:
        return RedirectResponse(url=f"{target}#error={quote('Token is missing an email claim')}")
    if not central_user_id:
        return RedirectResponse(url=f"{target}#error={quote('Token is missing a central user ID')}")

    central_profile: dict = {}
    if settings.CENTRAL_PROFILE_API_KEY:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                profile_resp = await client.get(
                    f"{settings.AUTH_SERVICE_BASE_URL}/barabari-auth/api/auth/internal/v1/students/{central_user_id}/profile",
                    headers={
                        "X-Internal-Api-Key": settings.CENTRAL_PROFILE_API_KEY,
                        "X-Product-Unique-Id": settings.SAMPARK_PRODUCT_UNIQUE_ID,
                    },
                )
                profile_body = (
                    profile_resp.json()
                    if profile_resp.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                if profile_resp.status_code == 200 and profile_body.get("isSuccess"):
                    central_profile = profile_body.get("data") or {}
            except httpx.HTTPError:
                # Login remains available during a registry outage. The stable ID is still
                # linked below and common fields can refresh on the next login.
                central_profile = {}

    try:
        user = await user_repo.get_user_by_email(email=email)
    except EntityDoesNotExist:
        # First time this student has reached Samvaad Saathi via Sampark Saathi SSO -
        # mirrors auth_cognito.py's authorize(), which likewise auto-created a local User
        # row keyed by email the first time a Cognito login came through. password_hash is
        # unused for SSO accounts (auth-service owns the credential), so a random value
        # keeps the NOT NULL column satisfied without a usable local password existing.
        random_password = secrets.token_urlsafe(16)
        name = " ".join(
            part for part in [central_profile.get("firstName"), central_profile.get("lastName")] if part
        ) or email.split("@")[0]
        user = await user_repo.create_user(email=email, password=random_password, name=name)

    await user_repo.sync_central_identity(
        user_id=user.id,
        central_user_id=str(central_user_id),
        name=" ".join(
            part for part in [central_profile.get("firstName"), central_profile.get("lastName")] if part
        ) or None,
        degree=central_profile.get("programType"),
        university=central_profile.get("institutionName"),
    )

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


@router.post("/request-access")
async def sso_request_access(payload: RequestAccessBody):
    """
    Central-auth plan, Phase 10: the "Request access" button on the "you don't have
    access yet" screen calls this. Same-origin from the frontend's perspective (this
    service proxies to auth-service server-to-server) rather than the frontend calling
    auth-service directly, since auth-service's CORS allow-list is built for Sampark
    Saathi's own domains, not this product's - and there's no bearer token to send anyway,
    only the one-time requestCode /callback captured above.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{settings.AUTH_SERVICE_BASE_URL}/barabari-auth/api/auth/public/v1/request-access-with-code",
                json={"code": payload.request_code},
            )
        except httpx.HTTPError as exc:
            raise fastapi.HTTPException(status_code=502, detail=f"Sampark Saathi unreachable: {exc}")

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code not in (200, 201) or not body.get("isSuccess"):
        raise fastapi.HTTPException(
            status_code=400, detail=body.get("errorMessage") or "Could not submit the access request"
        )

    return {"message": body.get("data", {}).get("message", "Access request submitted")}
