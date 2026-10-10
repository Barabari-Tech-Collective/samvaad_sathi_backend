"""An unconfigured auth service must fail loudly, never silently.

With AUTH_SERVICE_BASE_URL empty, the authorize redirect used to be built as a
relative path that resolved back to this service and answered 404, so every
student saw a dead "Continue" button and nothing explained it. A deployed
environment must refuse to start, and the SSO route must refuse to redirect.
"""

import fastapi
import pytest

from src.config.settings.environment import Environment
from src.main import verify_sso_configuration


@pytest.fixture
def settings_stub(monkeypatch):
    """Points both the guard and the route at the same patchable settings object."""
    import src.api.routes.auth_sso as auth_sso
    import src.main as main

    class Stub:
        AUTH_SERVICE_BASE_URL = ""
        ENVIRONMENT = Environment.DEVELOPMENT.value

    stub = Stub()
    monkeypatch.setattr(main, "settings", stub)
    monkeypatch.setattr(auth_sso, "settings", stub)
    return stub


@pytest.mark.parametrize("environment", [Environment.PRODUCTION.value, Environment.STAGING.value])
def test_deployed_environment_refuses_to_start_without_auth_service(settings_stub, environment):
    settings_stub.ENVIRONMENT = environment
    with pytest.raises(RuntimeError, match="AUTH_SERVICE_BASE_URL"):
        verify_sso_configuration()


def test_local_development_starts_but_reports_the_gap(settings_stub, caplog):
    settings_stub.ENVIRONMENT = Environment.DEVELOPMENT.value
    with caplog.at_level("ERROR"):
        verify_sso_configuration()
    assert "AUTH_SERVICE_BASE_URL" in caplog.text


@pytest.mark.parametrize("environment", [e.value for e in Environment])
def test_configured_auth_service_always_starts(settings_stub, environment):
    settings_stub.ENVIRONMENT = environment
    settings_stub.AUTH_SERVICE_BASE_URL = "https://auth.example.org"
    verify_sso_configuration()


@pytest.mark.anyio
async def test_sso_login_refuses_to_redirect_without_auth_service(settings_stub):
    """A student must get a stated error, not a redirect to a 404."""
    import src.api.routes.auth_sso as auth_sso

    request = fastapi.Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/sso/login",
            "headers": [],
            "query_string": b"",
            "session": {},
        }
    )
    with pytest.raises(fastapi.HTTPException) as raised:
        await auth_sso.sso_login(request)
    assert raised.value.status_code == fastapi.status.HTTP_503_SERVICE_UNAVAILABLE
    assert "unavailable" in raised.value.detail.lower()
