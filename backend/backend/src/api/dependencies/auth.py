import fastapi
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.config.manager import settings
from src.models.db.user import User
from src.repository.crud.user import UserCRUDRepository
from src.securities.authorizations.jwt import jwt_generator
from src.securities.authorizations.sso_jwt import decode_sso_access_token, SsoTokenError
from src.securities.authorizations.access_revocation import is_access_revoked
from src.api.dependencies.repository import get_repository
from src.utilities.exceptions.database import EntityDoesNotExist, EntityAlreadyExists
from src.utilities.server_timing import step

# Create HTTPBearer security scheme for Swagger UI
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = fastapi.Depends(optional_security),
    user_repo: UserCRUDRepository = fastapi.Depends(
        get_repository(repo_type=UserCRUDRepository)
    ),
    request: fastapi.Request = None,
) -> User:
    if request is not None:
        from src.api.routes.product_session import COOKIE
        has_cookie = COOKIE in request.cookies
        if has_cookie and getattr(settings, "PRODUCT_COOKIE_API_ENABLED", False):
            if request.url.path in {"/me", f"{getattr(settings, 'API_PREFIX', '/api').rstrip('/')}/me"}:
                raise fastapi.HTTPException(409, "Use the session profile endpoint.")
            # Never choose silently between two potentially different accounts.
            if request.headers.get("authorization") is not None:
                raise fastapi.HTTPException(400, "Use one authentication method per request.")
            from src.api.dependencies.product_session import get_session_user
            return await get_session_user(request=request, user_repo=user_repo)
    if credentials is None:
        raise fastapi.HTTPException(403, "Not authenticated")
    token = credentials.credentials
    if token.startswith("upload_grant_"):
        if request is None:
            raise fastapi.HTTPException(401, "Upload request required.")
        from src.services.upload_grant import consume
        cached = getattr(request.state, "resolved_upload_grant", None)
        if cached and cached[0] == token:
            token = cached[1]
        else:
            grant = token
            token = await consume(grant, request.url.path, request.method)
            request.state.resolved_upload_grant = (grant, token)

    # Central authentication is the only identity source. Tokens are issued by
    # auth-service; there is no locally signed fallback, so an unverifiable token is a
    # plain 401 rather than a second way in.
    token_source = "central_sso"
    try:
        claims = decode_sso_access_token(token)
        from src.services.central_identity_access import current_identity
        with step("central_auth"):
            claims = await current_identity(claims, token=token)
        email = claims.get("sub")
        if not email:
            raise SsoTokenError("Missing sub claim")

        # Entitlement is only checked at /authorize and /token (login/token-issuance
        # time) - this token could have been issued before a since-processed revoke, and
        # verification here never calls back to auth-service to find out. This local,
        # Redis-backed check (populated by the RabbitMQ consumer in
        # src/mqconsumer/user_access_revoked_consumer.py) closes that window without
        # adding a synchronous cross-service call to every request.
        unique_id = claims.get("userUniqueId")
        with step("redis_revoked"):
            revoked = bool(unique_id) and await is_access_revoked(unique_id)
        if revoked:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_403_FORBIDDEN,
                detail="Access to Samvaad Saathi has been revoked",
            )
    except SsoTokenError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    timer = step("db_user")
    timer.__enter__()
    try:
        if settings.CENTRAL_IDENTITY_CHECK_ENABLED:
            try:
                user = await user_repo.get_user_by_central_identity(claims["userUniqueId"])
            except EntityDoesNotExist:
                user = await user_repo.get_user_by_email(email=email)
        else:
            user = await user_repo.get_user_by_email(email=email)
    except EntityDoesNotExist:
        # First time this SSO-verified identity hits the Samvaad backend. Mirror
        # what auth_sso.py does on the /authorize callback: create a local row so
        # subsequent requests (analytics, interviews, etc.) can find the user.
        # auth_sso.py only runs when students go through the SSO interview link;
        # admin-only users (who only hit the dashboard) never trigger that path.
        name = (email or "").split("@")[0]
        try:
            user = await user_repo.create_user(email=email, password="", name=name)
        except EntityAlreadyExists:
            # Race between two concurrent first requests — re-fetch the now-existing row.
            user = await user_repo.get_user_by_email(email=email)
    except Exception:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="User not found in database",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.CENTRAL_IDENTITY_CHECK_ENABLED:
        if user.central_identity_id is None:
            central_role = str(claims.get("role", "")).replace("_", " ").upper().strip()
            if ((user.is_super_admin and central_role != "SUPER ADMIN") or
                (user.is_admin and central_role not in {"ADMIN", "SUPER ADMIN"})):
                raise fastapi.HTTPException(409, "Privileged legacy account binding requires administrator review.")
        try:
            user = await user_repo.bind_central_identity(user, claims["userUniqueId"])
        except EntityAlreadyExists:
            raise fastapi.HTTPException(409, "Central account binding needs administrator review.")
    # Covers lookup, first-time creation and central binding; an error path is not timed.
    timer.__exit__(None, None, None)
    setattr(user, "_token_source", token_source)

    # Attach token claims and synchronize role metadata on non-persistent attributes
    if claims:
        raw_role = str(claims.get("role") or "").strip().upper()
        norm_role = raw_role.replace("_", " ")
        setattr(user, "_token_claims", claims)
        setattr(user, "_token_role", raw_role)

        if token_source == "central_sso" and norm_role == "SUPER ADMIN":
            # Only set _token_is_super_admin if the user is not an explicit standard admin in DB
            if not (
                getattr(user, "is_admin", False)
                and not getattr(user, "is_super_admin", False)
            ):
                setattr(user, "_token_is_super_admin", True)
            setattr(user, "_token_is_admin", True)
        elif token_source == "central_sso" and norm_role == "ADMIN":
            setattr(user, "_token_is_admin", True)

    return user


async def get_optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = fastapi.Depends(
        optional_security
    ),
    user_repo: UserCRUDRepository = fastapi.Depends(
        get_repository(repo_type=UserCRUDRepository)
    ),
    request: fastapi.Request = None,
) -> User | None:
    """Like get_current_user but returns None instead of 401 when no token is provided."""
    if request is not None:
        from src.api.routes.product_session import COOKIE
        if COOKIE in request.cookies and getattr(settings, "PRODUCT_COOKIE_API_ENABLED", False):
            return await get_current_user(credentials=credentials, user_repo=user_repo, request=request)
    if not credentials:
        return None
    try:
        return await get_current_user(credentials=credentials, user_repo=user_repo, request=request)
    except fastapi.HTTPException:
        return None
