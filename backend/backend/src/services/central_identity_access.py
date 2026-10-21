"""Fresh central identity/product checks, explicitly enabled after bridge rollout."""

from urllib.parse import urlsplit, quote
import fastapi
import httpx
from src.config.manager import settings


async def current_identity(claims, *, token=None, transport=None):
    if not settings.CENTRAL_IDENTITY_CHECK_ENABLED:
        return claims
    base = settings.AUTH_SERVICE_BASE_URL.rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not settings.PRODUCT_IDENTITY_API_KEY
    ):
        raise fastapi.HTTPException(
            503, "Central identity verification is not configured."
        )
    uid = claims.get("userUniqueId")
    if not isinstance(uid, str) or not uid.strip() or len(uid) > 128:
        raise fastapi.HTTPException(
            401, "Sign in again through central authentication."
        )
    try:
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, transport=transport
        ) as client:
            response = await client.get(
                base
                + "/barabari-auth/api/auth/internal/v1/product-identities/"
                + quote(uid, safe="")
                + "/access",
                headers={
                    "Authorization": "Bearer " + (token or ""),
                    "X-Product-Identity-Key": settings.PRODUCT_IDENTITY_API_KEY,
                    "X-Product-Unique-Id": settings.SAMPARK_PRODUCT_UNIQUE_ID,
                },
            )
        if response.status_code in {401, 403, 404}:
            # 401 is the machine credential, not evidence that the user was revoked.
            if response.status_code == 401:
                if response.json().get("code") == "token_invalid":
                    raise fastapi.HTTPException(
                        401, "Sign in again through central authentication."
                    )
                raise fastapi.HTTPException(
                    503, "Central identity verification is unavailable."
                )
            raise fastapi.HTTPException(
                403, "Central identity or product access is unavailable."
            )
        response.raise_for_status()
        value = response.json()["data"]
        if (
            not isinstance(value, dict)
            or value.get("active") is not True
            or value.get("userUniqueId") != uid
            or value.get("productUniqueId") != settings.SAMPARK_PRODUCT_UNIQUE_ID
        ):
            raise ValueError("Invalid identity envelope")
        if (
            not isinstance(value.get("email"), str)
            or value["email"].strip().casefold()
            != str(claims.get("sub", "")).strip().casefold()
        ):
            raise fastapi.HTTPException(401, "Central account changed. Sign in again.")
        from src.securities.authorizations.sso_jwt import _allowed_roles

        role = value.get("role")
        if not isinstance(role, str) or role.upper().strip() not in _allowed_roles():
            raise fastapi.HTTPException(
                403, "Central account role is not permitted for this product."
            )
        return {**claims, "role": role, "userUniqueId": uid}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise fastapi.HTTPException(
            503, "Central identity verification is unavailable."
        )


async def resolve_existing_identity(email: str, *, transport=None):
    """Validate an existing central account before adding a product membership."""
    if not settings.CENTRAL_IDENTITY_CHECK_ENABLED:
        return None
    base = settings.AUTH_SERVICE_BASE_URL.rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not settings.PRODUCT_IDENTITY_API_KEY
    ):
        raise fastapi.HTTPException(
            503, "Central identity verification is not configured."
        )
    try:
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, transport=transport
        ) as client:
            response = await client.post(
                base + "/barabari-auth/api/auth/internal/v1/product-identities/resolve",
                headers={
                    "X-Product-Identity-Key": settings.PRODUCT_IDENTITY_API_KEY,
                    "X-Product-Unique-Id": settings.SAMPARK_PRODUCT_UNIQUE_ID,
                },
                json={"email": email.strip().lower()},
            )
        if response.status_code in {403, 404}:
            raise fastapi.HTTPException(
                422,
                "An active central account with Samvaad product access is required before adding this membership.",
            )
        response.raise_for_status()
        value = response.json()["data"]
        uid = value.get("userUniqueId")
        if (
            value.get("active") is not True
            or value.get("productUniqueId") != settings.SAMPARK_PRODUCT_UNIQUE_ID
            or not isinstance(uid, str)
            or not uid.strip()
            or len(uid) > 128
            or str(value.get("email", "")).strip().casefold()
            != email.strip().casefold()
        ):
            raise ValueError("Invalid identity envelope")
        return uid
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise fastapi.HTTPException(
            503, "Central identity verification is unavailable."
        )
