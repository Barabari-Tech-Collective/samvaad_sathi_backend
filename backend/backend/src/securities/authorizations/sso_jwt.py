import base64

from jose import jwt as jose_jwt, JWTError as JoseJWTError

from src.config.manager import settings

# auth-service (Node/jsonwebtoken) signs HS256 tokens using
# Buffer.from(secret, "base64") as the raw HMAC key - NOT the base64 string itself. This
# mirrors that exact decode so tokens minted by /student-login and /token verify here.
_SECRET_KEY_BYTES = base64.b64decode(settings.AUTH_SERVICE_JWT_SECRET)


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
    try:
        claims = jose_jwt.decode(token=token, key=_SECRET_KEY_BYTES, algorithms=["HS256"])
    except JoseJWTError as token_decode_error:
        raise SsoTokenError("Unable to decode Sampark Saathi access token") from token_decode_error

    required_role = (settings.SSO_REQUIRED_ROLE or "").strip()
    if required_role:
        token_role = str(claims.get("role") or "").strip()
        if token_role.upper() != required_role.upper():
            # Deliberately does not echo the role back to the caller.
            raise SsoTokenError("Token role is not permitted for this product")

    return claims
