import base64

from jose import jwt as jose_jwt, JWTError as JoseJWTError

from src.config.manager import settings

# auth-service (Node/jsonwebtoken) signs HS256 tokens using
# Buffer.from(secret, "base64") as the raw HMAC key - NOT the base64 string itself. This
# mirrors that exact decode so tokens minted by /student-login and /token verify here.
_SECRET_KEY_BYTES = base64.b64decode(settings.AUTH_SERVICE_JWT_SECRET)


class SsoTokenError(Exception):
    """Raised when a Sampark Saathi auth-service access token fails to verify."""


# Students are the product's own users; ADMIN/SUPER_ADMIN operate the /v2/analytics
# dashboard embedded in the Sampark Saathi admin console. Other staff roles
# (FACILITATOR, PROGRAM_MANAGER, PLACEMENT_COM) have no Samvaad surface today and can
# be added through SSO_ALLOWED_ROLES without a code change if that stops being true.
DEFAULT_SSO_ALLOWED_ROLES = "STUDENT,ADMIN,SUPER_ADMIN"


def _allowed_roles() -> set[str]:
    """Roles permitted to authenticate against this product, upper-cased.

    Precedence: SSO_ALLOWED_ROLES, then the single-value SSO_REQUIRED_ROLE it replaced,
    then DEFAULT_SSO_ALLOWED_ROLES. An environment still setting only the old variable
    therefore keeps exactly the behaviour it has today rather than silently widening to
    the new default.

    There is no "disable" value: an unrecognised or blank configuration falls back to
    the default rather than admitting every role, so a typo cannot quietly remove the
    product-entitlement guard.
    """
    raw = (getattr(settings, "SSO_ALLOWED_ROLES", "") or "").strip()
    if not raw:
        raw = (getattr(settings, "SSO_REQUIRED_ROLE", "") or "").strip()
    if not raw:
        raw = DEFAULT_SSO_ALLOWED_ROLES
    roles = {role.strip().upper() for role in raw.split(",") if role.strip()}
    return roles or {role.strip().upper() for role in DEFAULT_SSO_ALLOWED_ROLES.split(",")}


def decode_sso_access_token(token: str) -> dict:
    """
    Verifies and decodes an access token issued by auth-service's /student-login or
    /token endpoints. Claims: sub (email), role, actions, userUniqueId, iat, exp.

    Also enforces the role claim. Every Barabari product shares one JWT secret and the
    token carries no audience/product claim, so signature validity alone proves only
    "some Barabari account", not "an account entitled to this product" - without this
    check a token minted for a different product entirely authenticates here as an
    ordinary user. auth-service applies the equivalent guard on its own student refresh
    endpoint; this is the consuming side of that same rule.

    The allow-list holds more than STUDENT because the /v2/analytics dashboard is
    embedded in the Sampark Saathi admin console and arrives with a staff token.
    Admitting a role here only gets the caller as far as "authenticated"; reading
    cross-student data still requires get_current_admin_user.
    """
    try:
        claims = jose_jwt.decode(token=token, key=_SECRET_KEY_BYTES, algorithms=["HS256"])
    except JoseJWTError as token_decode_error:
        raise SsoTokenError("Unable to decode Sampark Saathi access token") from token_decode_error

    allowed_roles = _allowed_roles()
    if allowed_roles:
        token_role = str(claims.get("role") or "").strip().upper()
        if token_role not in allowed_roles:
            # Deliberately does not echo the role back to the caller.
            raise SsoTokenError("Token role is not permitted for this product")

    return claims
