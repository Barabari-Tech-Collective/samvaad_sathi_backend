import logging
import secrets
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl

import fastapi
import httpx
import pydantic
from fastapi import Request
from fastapi.responses import RedirectResponse

from src.config.manager import settings
from src.repository.redis_client import async_redis
from src.services import sso_transactions, sso_handoff
from src.api.dependencies.admin import is_admin_user
from src.api.dependencies.repository import get_repository
from src.repository.crud.user import UserCRUDRepository
from src.utilities.exceptions.database import EntityDoesNotExist
from src.securities.authorizations.sso_jwt import decode_sso_access_token, SsoTokenError


logger = logging.getLogger(__name__)


class RequestAccessBody(pydantic.BaseModel):
    request_code: str

router = fastapi.APIRouter(prefix="/auth/sso", tags=["users"])


@router.get("/login", name="auth_sso_login")
async def sso_login(request: Request, return_to: str | None = None):
    """
    Replaces /auth/cognito/login as the entry point a frontend button redirects to.
    Sends the browser to auth-service's /authorize, which either silently recognizes an
    existing cross-product session (shared barabari_sso cookie) or bounces to the central
    Sampark Saathi login page first.
    """
    # Checked before any state is minted or stored: without a configured auth
    # service the authorize URL below would be a relative path that resolves back
    # to this service and answers 404, which is the dead "Continue" button
    # students hit. Report the misconfiguration instead of redirecting into it.
    if not settings.AUTH_SERVICE_BASE_URL.strip():
        logger.error("Refusing SSO login: AUTH_SERVICE_BASE_URL is not configured")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is temporarily unavailable. Please try again later.",
        )

    transactional = getattr(settings, "SSO_TRANSACTION_ENABLED", False)
    v2 = getattr(settings, "SSO_V2_ENABLED", False)
    if v2 and not transactional:
        raise fastapi.HTTPException(503, "Secure sign-in transactions must be enabled.")
    binding = None
    pkce_challenge = None
    if transactional:
        try:
            sso_transactions.frontend_destination(settings.COGNITO_POST_LOGIN_REDIRECT_URL or "", "/")
        except ValueError:
            raise fastapi.HTTPException(503, "Sign-in destination is not configured.") from None
        callback = urlsplit(settings.SSO_REDIRECT_URI)
        if callback.scheme not in {"https", "http"} or not callback.netloc or callback.username or callback.password:
            raise fastapi.HTTPException(503, "Sign-in is temporarily unavailable.")
        if callback.scheme == "http" and callback.hostname not in {"localhost", "127.0.0.1"}:
            raise fastapi.HTTPException(503, "Sign-in requires a secure callback.")
        if sum(name.startswith(sso_transactions.COOKIE_PREFIX) for name in request.cookies) >= 8:
            raise fastapi.HTTPException(429, "Too many unfinished sign-ins. Finish one or wait five minutes.")
        try:
            state, binding = await sso_transactions.create(
                async_redis.client, return_to, protocol="v2" if v2 else "v1",
                product=settings.SAMPARK_PRODUCT_UNIQUE_ID, callback=settings.SSO_REDIRECT_URI)
            if v2:
                pkce_challenge = await sso_transactions.challenge(async_redis.client, state, binding)
        except Exception:
            raise fastapi.HTTPException(503, "Sign-in is temporarily unavailable. Please try again.") from None
    else:
        state = secrets.token_urlsafe(24)
        request.session["sso_state"] = state
        request.session["sso_destination"] = sso_transactions.safe_destination(return_to)

    # auth-service matches redirect_uri by exact string against its registered
    # allow-list, so prefer the explicitly-configured value. Deriving it from the
    # request is unsafe behind a TLS-terminating proxy: url_for() reads the request
    # scheme, which is http unless uvicorn trusts X-Forwarded-Proto, and the resulting
    # http:// URL silently fails to match the registered https:// entry.
    redirect_uri = settings.SSO_REDIRECT_URI or str(request.url_for("auth_sso_callback"))
    browser_origin = settings.AUTH_SERVICE_PUBLIC_BASE_URL.strip() or settings.AUTH_SERVICE_BASE_URL
    params = {"product": settings.SAMPARK_PRODUCT_UNIQUE_ID, "redirect_uri": redirect_uri, "state": state}
    if v2:
        params.update(code_challenge=pkce_challenge, code_challenge_method="S256")
    version = "v2" if v2 else "v1"
    authorize_url = f"{browser_origin.rstrip('/')}/barabari-auth/api/auth/public/{version}/authorize?" + urlencode(params)
    response = RedirectResponse(url=authorize_url)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if transactional:
        response.set_cookie(sso_transactions.cookie_name(state), binding,
                            max_age=sso_transactions.TTL_SECONDS, httponly=True,
                            secure=urlsplit(redirect_uri).scheme == "https", samesite="lax", path="/")
    return response


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
    upserts the local User row by email, and returns to the frontend. Opt-in v2 uses
    a browser-bound one-use handoff; legacy v1 retains query-string token delivery.
    """
    target = settings.COGNITO_POST_LOGIN_REDIRECT_URL or "/"
    transactional = getattr(settings, "SSO_TRANSACTION_ENABLED", False)
    transaction_cookie = None
    transaction = None

    def redirect(url: str):
        landing = urlsplit(url)
        if "error" in dict(parse_qsl(landing.fragment)):
            query = [(k, v) for k, v in parse_qsl(landing.query) if k != "flow"]
            query.append(("flow", "callback"))
            url = urlunsplit((landing.scheme, landing.netloc, landing.path, urlencode(query), landing.fragment))
        response = RedirectResponse(url=url)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        if transaction_cookie:
            response.delete_cookie(transaction_cookie, path="/", httponly=True, samesite="lax",
                                   secure=urlsplit(settings.SSO_REDIRECT_URI).scheme == "https")
        return response

    if transactional:
        try:
            transaction_cookie = sso_transactions.cookie_name(state)
            transaction = await sso_transactions.consume(async_redis.client, state, request.cookies.get(transaction_cookie))
        except ValueError:
            transaction = None
        except Exception:
            return redirect(f"{target}#error=Sign-in%20temporarily%20unavailable")
        if not transaction:
            return redirect(f"{target}#error=Invalid%20or%20expired%20sign-in")
        # Safe destination is carried through success only. Errors stay on the login target.
        try:
            sso_transactions.frontend_destination(target, transaction["destination"])
            landing = urlsplit(target)
            landing_query = [(k, v) for k, v in parse_qsl(landing.query) if k != "return_to"]
            landing_query.append(("return_to", transaction["destination"]))
            success_target = urlunsplit((landing.scheme, landing.netloc, landing.path, urlencode(landing_query), ""))
        except ValueError:
            raise fastapi.HTTPException(503, "Sign-in destination is not configured.") from None
    else:
        expected_state = request.session.pop("sso_state", None)
        destination = request.session.pop("sso_destination", None)
        if not state or not expected_state or state != expected_state:
            return redirect(f"{target}#error={quote('Invalid or missing state')}")
        success_target = target
        if destination is not None:
            landing = urlsplit(target)
            query = [(k, v) for k, v in parse_qsl(landing.query) if k != "return_to"]
            query.append(("return_to", sso_transactions.safe_destination(destination)))
            success_target = urlunsplit((landing.scheme, landing.netloc, landing.path, urlencode(query), ""))
    # auth-service's /authorize sends this instead of a code when the student is
    # authenticated but doesn't have Samvaad Saathi in their product entitlements
    # (central-auth Phase 6) - a real, expected outcome, not a protocol error, so it gets
    # its own message rather than falling through to the generic "missing code" one below.
    # requestCode (central-auth Phase 10) rides along in the same redirect: the frontend's
    # "Request access" button needs it to call /auth/sso/request-access below, since this
    # denied student has no bearer token at all to authenticate that call otherwise.
    if error == "access_denied":
        params = urlencode({"error": "access_denied", "requestCode": requestCode or ""})
        return redirect(f"{target}#{params}")
    if not code:
        return redirect(f"{target}#error={quote('Missing authorization code')}")

    payload = {"code": code}
    version = "v1"
    if transaction and transaction.get("protocol") == "v2":
        if not getattr(settings, "SSO_V2_ENABLED", False):
            return redirect(f"{target}#error=Sign-in%20configuration%20changed.%20Please%20restart.")
        version = "v2"
        payload.update(product=transaction["product"], redirect_uri=transaction["callback"], code_verifier=transaction["verifier"])
    elif getattr(settings, "SSO_V2_ENABLED", False):
        return redirect(f"{target}#error=Please%20restart%20sign-in")
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        try:
            resp = await client.post(
                f"{settings.AUTH_SERVICE_BASE_URL.rstrip('/')}/barabari-auth/api/auth/public/{version}/token",
                json=payload,
            )
        except httpx.HTTPError as exc:
            return redirect(f"{target}#error={quote('Central sign-in is temporarily unavailable')}")

    try:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
        data = body.get("data") if isinstance(body, dict) else None
        if resp.status_code != 200 or body.get("isSuccess") is not True or not isinstance(data, dict):
            raise ValueError("Invalid exchange")
        access_token, refresh_token = data.get("accessToken"), data.get("refreshToken")
        if not all(isinstance(token, str) and token.strip() for token in (access_token, refresh_token)):
            raise ValueError("Missing token pair")
    except (ValueError, TypeError, AttributeError):
        return redirect(f"{target}#error=Sign-in%20failed.%20Please%20try%20again.")

    try:
        claims = decode_sso_access_token(access_token)
    except SsoTokenError:
        return redirect(f"{target}#error={quote('Received an unverifiable token')}")

    email = claims.get("sub")
    central_user_id = claims.get("userUniqueId")
    if not email:
        return redirect(f"{target}#error={quote('Token is missing an email claim')}")
    if not central_user_id:
        return redirect(f"{target}#error={quote('Token is missing a central user ID')}")

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

    synced_degree = central_profile.get("ugProgramType")
    synced_university = central_profile.get("institutionName")
    await user_repo.sync_central_identity(
        user_id=user.id,
        central_user_id=str(central_user_id),
        name=" ".join(
            part for part in [central_profile.get("firstName"), central_profile.get("lastName")] if part
        ) or None,
        degree=synced_degree,
        university=synced_university,
    )
    # The central profile is the canonical source now (collect once, reuse everywhere) -
    # if it already has what Samvaad's own onboarding form would otherwise ask for, treat
    # onboarding as already done rather than making the student re-enter it here. A student
    # who reached SAMVAAD_SAATHI access before finishing Sampark Saathi's own profile step
    # still falls through to Samvaad's local onboarding as a fallback.
    if synced_degree and synced_university and not user.is_onboarded:
        await user_repo.set_onboarded(user_id=user.id, value=True)

    # Central authority is evaluated from the verified token on each request.
    # Do not turn an SSO role into a permanent product-admin database grant.
    if transaction and transaction.get("protocol") == "v2":
        try:
            ticket, handoff_binding = await sso_handoff.issue(
                async_redis.client, access_token, refresh_token, transaction["destination"])
        except Exception:
            return redirect(f"{target}#error=Sign-in%20temporarily%20unavailable")
        landing = urlsplit(target)
        # Ticket is not sufficient to redeem without the HttpOnly binding cookie.
        response = redirect(urlunsplit((landing.scheme, landing.netloc, landing.path, "flow=handoff", urlencode({"handoff": ticket}))))
        response.set_cookie(sso_handoff.COOKIE + ticket, handoff_binding, max_age=sso_handoff.TTL,
                            httponly=True, secure=urlsplit(settings.SSO_REDIRECT_URI).scheme == "https",
                            samesite="lax", path="/")
        return response

    parts = urlsplit(success_target)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in {"token", "refresh_token"}]
    query.extend([("token", access_token), ("refresh_token", refresh_token)])
    return redirect(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)))


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


class HandoffBody(pydantic.BaseModel):
    ticket: str = pydantic.Field(pattern=r"^[A-Za-z0-9_-]{43}$")


@router.post("/complete")
async def complete_sign_in(request: Request, payload: HandoffBody):
    from fastapi.responses import JSONResponse
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if not getattr(settings, "SSO_V2_ENABLED", False):
        return JSONResponse({"detail": "Restart sign-in."}, status_code=503, headers=headers)
    configured = urlsplit(settings.COGNITO_POST_LOGIN_REDIRECT_URL or "")
    expected_origin = urlunsplit((configured.scheme, configured.netloc, "", "", ""))
    if not configured.netloc or request.headers.get("origin") != expected_origin:
        return JSONResponse({"detail": "Sign-in origin not allowed."}, status_code=403, headers=headers)
    name = sso_handoff.COOKIE + payload.ticket
    try:
        result = await sso_handoff.redeem(async_redis.client, payload.ticket, request.cookies.get(name))
    except Exception:
        return JSONResponse({"detail": "Sign-in temporarily unavailable."}, status_code=503, headers=headers)
    response = JSONResponse(result or {"detail": "Sign-in expired. Please restart."}, status_code=200 if result else 401, headers=headers)
    response.delete_cookie(name, path="/", httponly=True, samesite="lax",
                           secure=urlsplit(settings.SSO_REDIRECT_URI).scheme == "https")
    return response
