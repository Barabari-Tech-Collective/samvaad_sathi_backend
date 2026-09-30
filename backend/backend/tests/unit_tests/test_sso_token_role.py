"""Role enforcement on Sampark Saathi SSO tokens.

Every Barabari product shares one JWT secret, and auth-service puts no
audience/product claim in the token. Signature validity therefore proves
only "some Barabari account" - not "an account entitled to this product".
Without the role check, a token minted for another product authenticates
here as an ordinary user. auth-service enforces the equivalent rule on its
own student refresh endpoint; this is the consuming side of it.

The allow-list is no longer STUDENT-only. The /v2/analytics dashboard is
embedded in the Sampark Saathi admin console and arrives with a staff
token, so gating on STUDENT made those endpoints unreachable by anyone:
get_current_admin_user demanded an admin while this check refused every
admin token. Admitting a role here only means "authenticated" - reading
cross-student data still requires get_current_admin_user.
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


def test_admin_token_is_accepted():
    """Reversed from the original STUDENT-only rule: the analytics dashboard is
    driven from the admin console, so an ADMIN token has to get past this check.
    Authorization for cross-student data is get_current_admin_user's job."""
    assert decode_sso_access_token(_mint("ADMIN", "admin@example.com"))["sub"] == "admin@example.com"


def test_super_admin_token_is_accepted():
    claims = decode_sso_access_token(_mint("SUPER_ADMIN", "super@example.com"))
    assert claims["sub"] == "super@example.com"


def test_owner_token_is_rejected():
    """OWNER is not a Sampark role code and appears in no allow-list."""
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(_mint("OWNER"))


def test_unlisted_staff_role_is_rejected():
    """Widening to admins must not widen to every staff role. FACILITATOR has no
    Samvaad surface, so it stays out until someone adds it to SSO_ALLOWED_ROLES."""
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(_mint("FACILITATOR"))


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


def test_legacy_required_role_still_narrows(monkeypatch):
    """An environment still setting only the old single-value variable keeps the
    behaviour it has today - it must not silently widen to the new default."""
    monkeypatch.setattr(sso_jwt.settings, "SSO_ALLOWED_ROLES", "")
    monkeypatch.setattr(sso_jwt.settings, "SSO_REQUIRED_ROLE", "STUDENT")
    assert decode_sso_access_token(_mint("STUDENT"))["sub"] == "student@example.com"
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(_mint("ADMIN"))


def test_allowed_roles_takes_precedence_over_legacy(monkeypatch):
    monkeypatch.setattr(sso_jwt.settings, "SSO_ALLOWED_ROLES", "STUDENT,ADMIN")
    monkeypatch.setattr(sso_jwt.settings, "SSO_REQUIRED_ROLE", "STUDENT")
    assert decode_sso_access_token(_mint("ADMIN", "admin@example.com"))["sub"] == "admin@example.com"


def test_blank_configuration_falls_back_rather_than_admitting_everything(monkeypatch):
    """There is no 'disable' value. A blank or comma-only setting must fall back to
    the default allow-list, so a typo cannot quietly remove the entitlement guard."""
    monkeypatch.setattr(sso_jwt.settings, "SSO_ALLOWED_ROLES", " , , ")
    monkeypatch.setattr(sso_jwt.settings, "SSO_REQUIRED_ROLE", "")
    assert decode_sso_access_token(_mint("STUDENT"))["sub"] == "student@example.com"
    with pytest.raises(SsoTokenError):
        decode_sso_access_token(_mint("FACILITATOR"))
