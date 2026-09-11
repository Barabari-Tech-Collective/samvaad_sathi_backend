import fastapi
import datetime
from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.starlette_client import OAuth
from authlib.integrations.base_client.errors import OAuthError
from urllib.parse import quote
import secrets

from src.config.manager import settings
from src.api.dependencies.repository import get_repository
from src.repository.crud.user import UserCRUDRepository
from src.repository.crud.session import SessionCRUDRepository
from src.utilities.exceptions.database import EntityDoesNotExist
from src.securities.authorizations.jwt import jwt_generator
from src.api.dependencies.auth import get_current_user


router = fastapi.APIRouter(prefix="/auth/cognito", tags=["users"])


def _get_oauth(request: Request) -> OAuth:
    oauth = OAuth()
    region = settings.COGNITO_REGION
    userpool_id = settings.COGNITO_USERPOOL_ID
    authority = f"https://cognito-idp.{region}.amazonaws.com/{userpool_id}"

    oauth.register(
        name="cognito",
        client_id=settings.COGNITO_CLIENT_ID,
        client_secret=settings.COGNITO_CLIENT_SECRET,
        server_metadata_url=f"{authority}/.well-known/openid-configuration",
        client_kwargs={"scope": settings.COGNITO_SCOPES},
    )
    return oauth


@router.get("/login")
async def login(request: Request, studentId: str | None = None):
    oauth = _get_oauth(request)
    redirect_uri = request.url_for("auth_cognito_authorize")
    # session, not the OAuth state param - authlib owns that for CSRF
    if studentId:
        request.session["pending_student_id"] = studentId
    return await oauth.cognito.authorize_redirect(request, redirect_uri)


@router.get("/authorize", name="auth_cognito_authorize")
async def authorize(
    request: Request,
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
    session_repo: SessionCRUDRepository = fastapi.Depends(get_repository(repo_type=SessionCRUDRepository)),
):
    oauth = _get_oauth(request)
    # If Cognito redirected back with an error, surface it gracefully
    err = request.query_params.get("error")
    if err:
        desc = request.query_params.get("error_description") or err
        target_err = settings.COGNITO_POST_LOGIN_REDIRECT_URL or "/"
        return RedirectResponse(url=f"{target_err}#error={quote(desc)}")

    try:
        token = await oauth.cognito.authorize_access_token(request)
    except OAuthError as exc:
        # Redirect back with error message instead of 500
        target_err = settings.COGNITO_POST_LOGIN_REDIRECT_URL or "/"
        message = exc.description or str(exc)
        return RedirectResponse(url=f"{target_err}#error={quote(message)}")

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    name = userinfo.get("name") or (email.split("@")[0] if email else None)

    # Persist/fetch local user and mint JWT
    if not email:
        target_err = settings.COGNITO_POST_LOGIN_REDIRECT_URL or "/"
        return RedirectResponse(url=f"{target_err}?error={quote('Missing email in userinfo')}")

    try:
        user = await user_repo.get_user_by_email(email=email)
    except EntityDoesNotExist:
        random_password = secrets.token_urlsafe(16)
        user = await user_repo.create_user(email=email, password=random_password, name=name or email)
        await session_repo.create_session(user_id=user.id)

    pending_student_id = request.session.pop("pending_student_id", None)
    if pending_student_id:
        user = await user_repo.set_student_id_if_unset(user_id=user.id, student_id=pending_student_id)

    # Mint existing JWT (carry Cognito subject if available) and store in session for retrieval
    cognito_sub = userinfo.get("sub")
    extra_claims = {"cognito_sub": cognito_sub} if cognito_sub else None
    jwt_token = jwt_generator.generate_access_token_for_user(user=user, extra_claims=extra_claims)
    # Also create an app refresh token via Session table
    refresh = await session_repo.create_session(user_id=user.id, expiry_minutes=settings.REFRESH_TOKEN_EXPIRY_MINUTES)
    request.session["jwt"] = jwt_token
    request.session["user"] = {"email": email, "sub": userinfo.get("sub"), "name": name or email}

    # Optional: redirect to configured URL (frontend) or default
    target = settings.COGNITO_POST_LOGIN_REDIRECT_URL or "/"
    # Return tokens via URL fragment to reduce CSRF exposure
    return RedirectResponse(url=f"{target}?token={quote(jwt_token)}&refresh_token={quote(refresh.token)}")


@router.get("/logout")
async def logout(request: Request):
    # Clear server-side session entries
    request.session.pop("user", None)
    request.session.pop("jwt", None)
    request.session.pop("cognito_tokens", None)

    # If a Cognito Hosted UI domain is configured, redirect to its logout URL
    # Cognito requires client_id and logout_uri (post_logout_redirect_uri)
    domain = (settings.COGNITO_HOSTED_UI_DOMAIN or "").strip() if settings.COGNITO_HOSTED_UI_DOMAIN else ""
    is_placeholder = domain.lower() in {"none", "null", "false"}
    if domain and not is_placeholder and settings.COGNITO_CLIENT_ID:
        post_logout = settings.COGNITO_POST_LOGOUT_REDIRECT_URL or "/"
        # Accept both bare domain and full URL for hosted UI domain
        if domain.startswith("http://") or domain.startswith("https://"):
            base = domain.rstrip("/")
        else:
            base = f"https://{domain}".rstrip("/")
        logout_url = (
            f"{base}/logout?client_id={settings.COGNITO_CLIENT_ID}&logout_uri={quote(post_logout)}"
        )
        return RedirectResponse(url=logout_url)

    # Fallback: just redirect locally
    target = settings.COGNITO_POST_LOGOUT_REDIRECT_URL or "/"
    return RedirectResponse(url=target)


@router.get("/session")
async def get_session_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise fastapi.HTTPException(status_code=401, detail="Not authenticated")
    return {"user": user}


@router.get("/jwt")
async def get_session_jwt(request: Request):
    token = request.session.get("jwt")
    if not token:
        raise fastapi.HTTPException(status_code=401, detail="Not authenticated")
    # Best-effort: also include refresh token from DB session if stored earlier
    return {"token": token}


@router.post("/refresh")
async def refresh_access_token(
    refresh_token: str = fastapi.Form(...),
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
    session_repo: SessionCRUDRepository = fastapi.Depends(get_repository(repo_type=SessionCRUDRepository)),
):
    # Validate refresh token exists and is not expired
    session = await session_repo.get_session_by_token(token=refresh_token)
    if not session or session.expiry < datetime.datetime.now(datetime.timezone.utc):
        raise fastapi.HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await user_repo.get_user_by_id(user_id=session.user_id)
    if not user:
        raise fastapi.HTTPException(status_code=401, detail="User not found")

    # Rotate refresh token: create a new one and delete the previous session
    new_refresh = await session_repo.create_session(
        user_id=user.id, expiry_minutes=settings.REFRESH_TOKEN_EXPIRY_MINUTES
    )
    await session_repo.delete_session_by_token(token=refresh_token)

    new_access = jwt_generator.generate_access_token_for_user(user=user)
    return {"token": new_access, "refresh_token": new_refresh.token}


