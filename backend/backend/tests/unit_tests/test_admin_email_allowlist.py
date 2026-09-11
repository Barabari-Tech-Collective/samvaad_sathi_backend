"""ADMIN_EMAILS allow-list as a second route to analytics access.

The is_admin column defaults to false for every pre-existing row, so
granting it requires an UPDATE against the database - which whoever
configures the deployment may not be able to run (Render's free tier has
no shell, and DB console access isn't always shared with the person who
owns the service). This allow-list lets the dashboard be authorised via
an env var instead, without loosening anything for students.

The negative cases below are the ones that matter: a misconfigured or
empty allow-list must deny, never default-allow.
"""

import fastapi
import pytest

from src.api.dependencies import admin as admin_module
from src.api.dependencies.admin import get_current_admin_user, is_admin_user


class _User:
    def __init__(self, email: str = "", is_admin: bool = False, user_id: int = 1):
        self.id = user_id
        self.email = email
        self.is_admin = is_admin


def _set_allowlist(monkeypatch, value: str) -> None:
    monkeypatch.setattr(admin_module.settings, "ADMIN_EMAILS", value)


def test_email_on_the_allowlist_is_admin(monkeypatch):
    _set_allowlist(monkeypatch, "admin@samvaad-sathi.com")
    assert is_admin_user(_User(email="admin@samvaad-sathi.com")) is True


def test_student_not_on_the_allowlist_is_not_admin(monkeypatch):
    _set_allowlist(monkeypatch, "admin@samvaad-sathi.com")
    assert is_admin_user(_User(email="student@example.com")) is False


def test_empty_allowlist_grants_nobody(monkeypatch):
    """The default. An unset ADMIN_EMAILS must not mean 'allow everyone'."""
    _set_allowlist(monkeypatch, "")
    assert is_admin_user(_User(email="admin@samvaad-sathi.com")) is False
    assert is_admin_user(_User(email="")) is False


def test_user_with_empty_email_never_matches(monkeypatch):
    """Guards against '' accidentally matching a blank allow-list entry."""
    _set_allowlist(monkeypatch, "admin@samvaad-sathi.com,,")
    assert is_admin_user(_User(email="")) is False


def test_matching_is_case_insensitive(monkeypatch):
    _set_allowlist(monkeypatch, "Admin@Samvaad-Sathi.COM")
    assert is_admin_user(_User(email="admin@samvaad-sathi.com")) is True


def test_whitespace_and_trailing_commas_are_tolerated(monkeypatch):
    _set_allowlist(monkeypatch, "  admin@samvaad-sathi.com , ops@example.com , ")
    assert is_admin_user(_User(email="ops@example.com")) is True
    assert is_admin_user(_User(email="admin@samvaad-sathi.com")) is True
    assert is_admin_user(_User(email="someone@example.com")) is False


def test_db_flag_still_works_independently_of_the_allowlist(monkeypatch):
    """The column remains the primary mechanism; the env var is additive."""
    _set_allowlist(monkeypatch, "")
    assert is_admin_user(_User(email="someone@example.com", is_admin=True)) is True


@pytest.mark.asyncio
async def test_dependency_allows_allowlisted_user(monkeypatch):
    _set_allowlist(monkeypatch, "admin@samvaad-sathi.com")
    user = _User(email="admin@samvaad-sathi.com")
    assert await get_current_admin_user(current_user=user) is user


@pytest.mark.asyncio
async def test_dependency_still_403s_for_a_student(monkeypatch):
    _set_allowlist(monkeypatch, "admin@samvaad-sathi.com")
    with pytest.raises(fastapi.HTTPException) as exc:
        await get_current_admin_user(current_user=_User(email="student@example.com"))
    assert exc.value.status_code == 403
