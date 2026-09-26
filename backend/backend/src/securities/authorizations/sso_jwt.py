import base64

from jose import jwt as jose_jwt, JWTError as JoseJWTError

from src.config.manager import settings

import binascii

if not settings.AUTH_SERVICE_JWT_SECRET:
    raise ValueError("AUTH_SERVICE_JWT_SECRET is not set. The app cannot securely verify tokens without it.")

def _add_keys(secret: str, keys_list: list):
    if not secret:
        return
    keys_list.append(secret.encode("utf-8"))
    try:
        decoded = base64.b64decode(secret, validate=True)
        keys_list.append(decoded)
    except binascii.Error:
        pass

_VALID_KEYS = []
_add_keys(settings.AUTH_SERVICE_JWT_SECRET, _VALID_KEYS)
_add_keys(settings.AUTH_SERVICE_STAGING_JWT_SECRET, _VALID_KEYS)


class SsoTokenError(Exception):
    """Raised when a Sampark Saathi auth-service access token fails to verify."""


def decode_sso_access_token(token: str) -> dict:
    """
    Verifies and decodes an access token issued by auth-service's /student-login or
    /token endpoints. Claims: sub (email), role, actions, userUniqueId, iat, exp.

    Also enforces the role claim. Every Barabari product shares one JWT secret and the
    token carries no audience/product claim, so signature validity alone proves only
    "some Barabari account". Samvaad Saathi restricts access using SSO_REQUIRED_ROLE 
    (e.g., to STUDENT, ADMIN, SUPER_ADMIN) so tokens minted for completely different
    products or unpermitted roles authenticate successfully only if their role matches.
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

    required_role_str = (settings.SSO_REQUIRED_ROLE or "").strip()
    if required_role_str:
        allowed_roles = {r.strip().upper() for r in required_role_str.split(",") if r.strip()}
        token_role = str(claims.get("role") or "").strip().upper()
        if token_role not in allowed_roles:
            # Deliberately does not echo the role back to the caller.
            raise SsoTokenError("Token role is not permitted for this product")

    return claims
