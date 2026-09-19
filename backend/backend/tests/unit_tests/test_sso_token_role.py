"""Role enforcement on Sampark Saathi SSO tokens.

Every Barabari product shares one JWT secret, and auth-service puts no
audience/product claim in the token. Signature validity therefore proves
only "some Barabari account" - not "a student entitled to this product".
Without the role check, a token minted for another product, or an
ADMIN/OWNER token from the admin panel, authenticates here as an ordinary
user. auth-service enforces the equivalent rule on its own student
refresh endpoint; this is the consuming side of it.
"""

import base64
import time

import pytest
from jose import jwt as jose_jwt

from src.config.manager import settings
from src.securities.authorizations import sso_jwt
from src.securities.authorizations.sso_jwt import SsoTokenError, decode_sso_access_token


def _mint(role: str | None, email: str = "student@example.com") -> str:
    """Sign a token exactly the way auth-service does: HS256 over the
    base64-DECODED secret, email in `sub`."""
    claims: dict = {"sub": email, "exp": int(time.time()) + 3600}
    if role is not None:
        claims["role"] = role
    return jose_jwt.encode(
        claims,
        base64.b64decode(settings.AUTH_SERVICE_JWT_SECRET),
        algorithm="HS256",
    )


def test_student_token_is_accepted():
    claims = decode_sso_access_token(_mint("STUDENT"))
    assert claims["sub"] == "student@example.com"


def test_admin_token_from_the_admin_panel_is_rejected():
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(_mint("ADMIN"))


def test_owner_token_is_rejected():
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(_mint("OWNER"))


def test_token_with_no_role_claim_is_rejected():
    """Fail closed: a token that simply omits `role` must not slip through."""
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(_mint(None))


def test_role_check_is_case_insensitive():
    assert decode_sso_access_token(_mint("student"))["sub"] == "student@example.com"


def test_token_signed_with_a_different_secret_is_rejected():
    """Guards the underlying signature check, not just the role branch."""
    forged = jose_jwt.encode(
        {"sub": "attacker@example.com", "role": "STUDENT", "exp": int(time.time()) + 3600},
        b"not-the-shared-secret",
        algorithm="HS256",
    )
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(forged)


def test_expired_token_is_rejected():
    expired = jose_jwt.encode(
        {"sub": "student@example.com", "role": "STUDENT", "exp": int(time.time()) - 60},
        base64.b64decode(settings.AUTH_SERVICE_JWT_SECRET),
        algorithm="HS256",
    )
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(expired)


def test_role_check_can_be_disabled(monkeypatch):
    """Escape hatch if another role legitimately needs access later."""
    monkeypatch.setattr(sso_jwt.settings, "SSO_REQUIRED_ROLE", "")
    assert decode_sso_access_token(_mint("ADMIN"))["sub"] == "student@example.com"
