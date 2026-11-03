"""Opt-in session lifecycle HTTP boundary; product API transport migration is separate."""
import secrets
import hashlib
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from src.config.manager import settings
from src.repository.redis_client import async_redis
from src.services.product_session import ProductSessions, MAX_TTL
from src.services import sso_handoff
from src.services.sso_transactions import safe_destination
from src.services.central_identity_access import current_identity
from src.securities.authorizations.sso_jwt import decode_sso_access_token, SsoTokenError
from src.securities.authorizations.access_revocation import is_access_revoked
from fastapi import HTTPException

class SessionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def guarded(request):
            try:
                response = await handler(request)
            except HTTPException as exc:
                response = error_response(exc)
            except RequestValidationError:
                # Never echo submitted handoff tickets in validation diagnostics.
                response = reply({"detail": "Invalid session request."}, 422)
            except Exception:
                response = reply({"detail": "Session service temporarily unavailable."}, 503)
            vary = response.headers.get("Vary", "")
            response.headers.update(HEADERS)
            response.headers["Vary"] = ", ".join(dict.fromkeys([v.strip() for v in vary.split(",") if v.strip()] + ["Origin", "Cookie"]))
            return response
        return guarded


router = APIRouter(prefix="/auth/session", tags=["users"], route_class=SessionRoute)
COOKIE = "__Host-samvaad_session"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Vary": "Origin, Cookie", "X-Content-Type-Options": "nosniff"}


def reply(data, status=200):
    return JSONResponse(data, status_code=status, headers=HEADERS)


def boundary(request):
    if not all(getattr(settings, flag, False) for flag in (
        "PRODUCT_SESSION_ENABLED", "SSO_V2_ENABLED", "SSO_TRANSACTION_ENABLED", "CENTRAL_IDENTITY_CHECK_ENABLED"
    )):
        raise HTTPException(503, "Session sign-in is not enabled.")
    def configured_url(value):
        if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError("Invalid URL")
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.port == 0):
            raise ValueError("Invalid URL")
        return parsed
    try:
        frontend = configured_url(settings.COGNITO_POST_LOGIN_REDIRECT_URL)
        configured_url(settings.SSO_REDIRECT_URI)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(503, "Secure session origins are not configured.") from None
    expected = urlunsplit((frontend.scheme, frontend.netloc, "", "", ""))
    if len(request.headers.getlist("origin")) != 1 or request.headers.get("origin") != expected:
        raise HTTPException(403, "Session origin not allowed.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site session request not allowed.")
    # Reject cookie shadowing rather than accepting whichever parser entry wins.
    cookie_names = [part.strip().split("=", 1)[0] for header in request.headers.getlist("cookie") for part in header.split(";")]
    for name in set(cookie_names):
        if (name == COOKIE or name.startswith(sso_handoff.COOKIE)) and cookie_names.count(name) > 1:
            raise HTTPException(400, "Ambiguous session cookies. Clear site cookies and sign in again.")
    try:
        keys = [key.strip() for key in settings.PRODUCT_SESSION_ENCRYPTION_KEYS.split(",") if key.strip()]
        return ProductSessions(async_redis.client, keys)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(503, "Session storage is not configured.") from None


def check_csrf(request, session):
    supplied = request.headers.get("x-csrf-token", "")
    if len(request.headers.getlist("x-csrf-token")) != 1 or not supplied or len(supplied) > 128 or not secrets.compare_digest(supplied.encode(), session.csrf.encode()):
        raise HTTPException(403, "Session changed. Reload before continuing.")


async def verified_principal(token):
    try:
        claims = decode_sso_access_token(token)
    except SsoTokenError:
        raise HTTPException(401, "Sign in again.") from None
    claims = await current_identity(claims, token=token)
    principal = claims.get("userUniqueId")
    if not isinstance(principal, str) or not principal.strip() or len(principal) > 128 or principal != principal.strip():
        raise HTTPException(401, "Sign in again.")
    if await is_access_revoked(principal):
        raise HTTPException(403, "Product access was revoked.")
    return principal


def error_response(exc):
    if isinstance(exc, HTTPException):
        return reply({"detail": exc.detail}, exc.status_code)
    return reply({"detail": "Session service temporarily unavailable."}, 503)


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


@router.post("/complete")
async def complete(request: Request, payload: Completion):
    new_id = None
    store = None
    try:
        store = boundary(request)
        old_id = request.cookies.get(COOKIE)
        old = await store.read(old_id)
        if old:
            check_csrf(request, old)
        name = sso_handoff.COOKIE + payload.ticket
        result = await sso_handoff.redeem(async_redis.client, payload.ticket, request.cookies.get(name))
        if result is None:
            raise HTTPException(401, "Sign-in expired. Please restart.")
        principal = await verified_principal(result["token"])
        new_id, session = await store.create(principal, result["token"], result["refresh_token"])
        await store.revoke(old_id)
        response = reply({"authenticated": True, "csrf_token": session.csrf, "return_to": safe_destination(result["return_to"])})
        response.set_cookie(COOKIE, new_id, httponly=True, secure=True, samesite="lax", path="/", max_age=MAX_TTL)
        response.delete_cookie(name, httponly=True, secure=True, samesite="lax", path="/")
        return response
    except Exception as exc:
        if new_id and store:
            try:
                await store.revoke(new_id)
            except Exception:
                pass  # No cookie was issued; unreachable record expires at its absolute TTL.
        return error_response(exc)


@router.get("")
async def status(request: Request):
    session = None
    try:
        store = boundary(request)
        sid = request.cookies.get(COOKIE)
        session = await store.read(sid)
        if session is None:
            return reply({"authenticated": False}, 401)
        principal = await verified_principal(session.access_token)
        if principal != session.principal:
            await store.revoke(sid)
            raise HTTPException(401, "Session identity changed. Sign in again.")
        # Identity is for cache isolation only, not role/resource authorization.
        return reply({"authenticated": True, "principal": principal, "csrf_token": session.csrf})
    except Exception as exc:
        if session and isinstance(exc, HTTPException) and exc.status_code in {401, 403}:
            # Same-origin caller can still log out after access-token expiry/revocation.
            return reply({"authenticated": False, "csrf_token": session.csrf, "detail": exc.detail}, exc.status_code)
        return error_response(exc)


@router.post("/logout")
async def logout(request: Request):
    try:
        store = boundary(request)
        sid = request.cookies.get(COOKIE)
        session = await store.read(sid)
        if session:
            check_csrf(request, session)
        # Logout remains possible after access-token expiry or central-service outage.
        await store.revoke(sid)
        response = reply({"authenticated": False})
        response.delete_cookie(COOKIE, httponly=True, secure=True, samesite="lax", path="/")
        return response
    except Exception as exc:
        return error_response(exc)


@router.post("/refresh")
async def refresh_session(request: Request):
    from src.services.product_session_refresh import refresh
    try:
        store = boundary(request)
        sid = request.cookies.get(COOKIE)
        session = await store.read(sid)
        if session is None:
            raise HTTPException(401, "Sign in again.")
        check_csrf(request, session)
        await refresh(store, sid, verified_principal)
        return reply({"refreshed": True})
    except Exception as exc:
        return error_response(exc)


@router.post("/logout-all")
async def logout_all(request: Request):
    from src.services.central_session_logout import revoke_everywhere
    from src.services.product_session_refresh import refresh
    try:
        store = boundary(request)
        sid = request.cookies.get(COOKIE)
        csrf = request.headers.get("x-csrf-token", "")
        receipt_key = None
        if isinstance(sid, str) and len(sid) <= 128 and csrf and len(csrf) <= 128:
            receipt_key = "product-global-logout:" + hashlib.sha256((sid + ":" + csrf).encode()).hexdigest()
            if await async_redis.client.get(receipt_key):
                response = reply({"authenticated": False, "globally_revoked": True})
                response.delete_cookie(COOKIE, httponly=True, secure=True, samesite="lax", path="/")
                return response
        session = await store.read(sid)
        if session is None:
            raise HTTPException(401, "Sign in again to revoke other sessions.")
        check_csrf(request, session)
        # Retry the original signed lineage first. Central can acknowledge a completed
        # revocation even after expiry; it never revokes a newer login on this path.
        try:
            await revoke_everywhere(session.access_token)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            await refresh(store, sid, verified_principal)
            session = await store.read(sid)
            if session is None:
                raise HTTPException(401, "Session ended.")
            if await verified_principal(session.access_token) != session.principal:
                raise HTTPException(401, "Session identity changed.")
            await revoke_everywhere(session.access_token)
        if receipt_key:
            await async_redis.client.set(receipt_key, "confirmed", ex=MAX_TTL)
        await store.revoke(sid)
        response = reply({"authenticated": False, "globally_revoked": True})
        response.delete_cookie(COOKIE, httponly=True, secure=True, samesite="lax", path="/")
        return response
    except Exception as exc:
        return error_response(exc)
