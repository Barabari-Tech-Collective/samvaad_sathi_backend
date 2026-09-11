import fastapi
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.config.manager import settings
from src.models.db.user import User
from src.repository.crud.user import UserCRUDRepository
from src.securities.authorizations.jwt import jwt_generator
from src.securities.authorizations.sso_jwt import decode_sso_access_token, SsoTokenError
from src.api.dependencies.repository import get_repository

# Create HTTPBearer security scheme for Swagger UI
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = fastapi.Depends(security),
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
) -> User:
    token = credentials.credentials

    # Tokens now come from auth-service (student-login/SSO) rather than this service's own
    # Cognito/local-password flow. Tried first since that's the path all new logins take;
    # falls back to the legacy verifier below so sessions minted before this migration (and
    # not yet re-authenticated through Sampark Saathi) keep working until they expire.
    try:
        claims = decode_sso_access_token(token)
        email = claims.get("sub")
        if not email:
            raise SsoTokenError("Missing sub claim")
    except SsoTokenError:
        try:
            _, email = jwt_generator.retrieve_details_from_token(token=token, secret_key=settings.JWT_SECRET_KEY)
        except Exception:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    try:
        user = await user_repo.get_user_by_email(email=email)
        if not user:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except Exception:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="User not found in database",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user


