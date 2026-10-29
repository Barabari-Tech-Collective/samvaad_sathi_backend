"""Fresh central identity/product checks, explicitly enabled after bridge rollout."""

import contextlib
import hashlib
import time
from urllib.parse import urlsplit, quote
import fastapi
import httpx
from src.config.manager import settings

# One pooled client per process: a fresh client per request paid a TCP+TLS handshake to
# auth-service on every authenticated call.
_shared_client: httpx.AsyncClient | None = None

# Optional short positive cache of *verified* identities (off by default, see
# CENTRAL_IDENTITY_CACHE_SECONDS). Failures are never cached and an outage still fails closed,
# but while enabled a central revoke is only seen after the TTL unless the Redis flag arrives.
_identity_cache: dict[str, tuple[float, dict]] = {}
_CACHE_MAX_ENTRIES = 4096


def _pooled_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _shared_client


@contextlib.asynccontextmanager
async def _client_for(transport):
    if transport is not None:  # tests inject a transport; keep that path isolated
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False, transport=transport) as client:
            yield client
    else:
        yield _pooled_client()


async def close_identity_client() -> None:
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


def _cache_key(uid: str, token: str | None) -> str:
    return hashlib.sha256(f"{uid}\x00{token or ''}".encode()).hexdigest()


def _cache_get(key: str):
    hit = _identity_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return dict(hit[1])
    _identity_cache.pop(key, None)
    return None


def _cache_put(key: str, value: dict) -> None:
    ttl = float(getattr(settings, "CENTRAL_IDENTITY_CACHE_SECONDS", 0) or 0)
    if ttl <= 0:
        return
    if len(_identity_cache) >= _CACHE_MAX_ENTRIES:
        now = time.monotonic()
        for stale in [k for k, (exp, _) in _identity_cache.items() if exp <= now]:
            _identity_cache.pop(stale, None)
        if len(_identity_cache) >= _CACHE_MAX_ENTRIES:
            _identity_cache.clear()
    _identity_cache[key] = (time.monotonic() + ttl, dict(value))


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
    cache_key = _cache_key(uid, token) if transport is None else None
    if cache_key and (cached := _cache_get(cache_key)) is not None:
        return cached
    try:
        async with _client_for(transport) as client:
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
        verified = {**claims, "role": role, "userUniqueId": uid}
        if cache_key:
            _cache_put(cache_key, verified)
        return verified
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
