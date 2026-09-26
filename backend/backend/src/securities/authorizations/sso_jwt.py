import base64

from jose import jwt as jose_jwt, JWTError as JoseJWTError

from src.config.manager import settings

# The Java Spring Boot auth service might use raw bytes instead of base64.
# We will try both the base64 decoded bytes and the raw UTF-8 string bytes.
_VALID_KEYS = [
    base64.b64decode(settings.AUTH_SERVICE_JWT_SECRET),
    settings.AUTH_SERVICE_JWT_SECRET.encode("utf-8")
]
if settings.AUTH_SERVICE_STAGING_JWT_SECRET:
    _VALID_KEYS.extend([
        base64.b64decode(settings.AUTH_SERVICE_STAGING_JWT_SECRET),
        settings.AUTH_SERVICE_STAGING_JWT_SECRET.encode("utf-8")
    ])


class SsoTokenError(Exception):
    """Raised when a Sampark Saathi auth-service access token fails to verify."""


def decode_sso_access_token(token: str) -> dict:
    """
    Verifies and decodes an access token issued by auth-service's /student-login or
    /token endpoints. Claims: sub (email), role, actions, userUniqueId, iat, exp.

    Also enforces the role claim. Every Barabari product shares one JWT secret and the
    token carries no audience/product claim, so signature validity alone proves only
    "some Barabari account", not "a student entitled to this product" - without this
    check an ADMIN/OWNER token from the admin panel, or a token minted for a different
    product entirely, authenticates here as an ordinary user. auth-service applies the
    equivalent guard on its own student refresh endpoint; this is the consuming side of
    that same rule.
    """


    claims = None
    last_error = None
    for key in _VALID_KEYS:
        try:
            claims = jose_jwt.decode(token=token, key=key, algorithms=["HS256", "HS384", "HS512"])
            break
        except JoseJWTError as e:
            last_error = e
            continue
            
    if claims is None:
        raise SsoTokenError(f"Unable to decode Sampark Saathi access token: {repr(last_error)}")

    required_role = (settings.SSO_REQUIRED_ROLE or "").strip()
    if required_role:
        token_role = str(claims.get("role") or "").strip()
        if token_role.upper() != required_role.upper():
            # Deliberately does not echo the role back to the caller.
            raise SsoTokenError("Token role is not permitted for this product")

    return claims
