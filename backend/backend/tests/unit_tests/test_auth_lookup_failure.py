"""A database failure during user lookup must not look like a signed-out user.

get_current_user used to answer 401 "User not found in database" for *any* exception
raised while looking the user up, including pool timeouts and dropped connections. The
frontend reads 401 as "session expired" and returns the person to the login page, so a
flaky database showed up as staff being unable to sign in.
"""

import base64
import time
from types import SimpleNamespace

import fastapi
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt as jose_jwt

from src.api.dependencies import auth
from src.config.manager import settings
from src.utilities.exceptions.database import EntityDoesNotExist


def _credentials(role: str = "STAFF") -> HTTPAuthorizationCredentials:
    token = jose_jwt.encode(
        {"sub": "member@example.com", "role": role, "userUniqueId": "uid-1", "exp": int(time.time()) + 3600},
        base64.b64decode(settings.AUTH_SERVICE_JWT_SECRET),
        algorithm="HS256",
    )
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class _Repo:
    def __init__(self, error: Exception):
        self.error = error

    async def get_user_by_central_identity(self, _central_id):
        raise self.error

    async def get_user_by_email(self, *, email):
        raise self.error


@pytest.fixture(autouse=True)
def _no_central_call(monkeypatch):
    async def passthrough(claims, *, token=None, transport=None):
        return claims

    monkeypatch.setattr("src.services.central_identity_access.current_identity", passthrough)

    async def not_revoked(_unique_id):
        return False

    monkeypatch.setattr(auth, "is_access_revoked", not_revoked)


@pytest.mark.asyncio
async def test_database_failure_is_retryable_not_signed_out():
    with pytest.raises(fastapi.HTTPException) as caught:
        await auth.get_current_user(
            credentials=_credentials(),
            user_repo=_Repo(ConnectionError("pool exhausted")),
            request=None,
        )
    assert caught.value.status_code == 503
    assert caught.value.status_code != 401


@pytest.mark.asyncio
async def test_invalid_token_is_still_401():
    bad = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-token")
    with pytest.raises(fastapi.HTTPException) as caught:
        await auth.get_current_user(credentials=bad, user_repo=_Repo(ConnectionError()), request=None)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_unpermitted_role_is_still_401():
    with pytest.raises(fastapi.HTTPException) as caught:
        await auth.get_current_user(
            credentials=_credentials("OWNER"),
            user_repo=_Repo(EntityDoesNotExist("x")),
            request=None,
        )
    assert caught.value.status_code == 401
