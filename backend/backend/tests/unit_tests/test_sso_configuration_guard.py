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
        AUTH_SERVICE_PUBLIC_BASE_URL = ""
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


def test_local_development_starts_but_reports_the_gap(settings_stub, monkeypatch):
    """Substitutes the logger rather than capturing from the logging system.

    Two earlier attempts were defeated by global logging state:
    initialize_backend_application() calls logging.basicConfig(force=True),
    which drops the handler caplog installs, and Loki configuration rearranges
    handlers again. Either way the assertion passed or failed on test ordering
    rather than on behaviour. Recording what the function asks the logger to do
    is deterministic, and the behaviour under test is "it reports the gap".
    """
    import src.main as main

    settings_stub.ENVIRONMENT = Environment.DEVELOPMENT.value
    reported: list[str] = []

    class Recorder:
        def error(self, message, *args):
            reported.append(message % args if args else message)

    # Replaces only this module's logger. Patching logging.getLogger itself
    # handed pytest's own logging plugin a Recorder and crashed the run.
    monkeypatch.setattr(main, "logger", Recorder())

    verify_sso_configuration()  # must not raise in development

    assert any("AUTH_SERVICE_BASE_URL" in message for message in reported)


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


@pytest.mark.anyio
@pytest.mark.parametrize("public_origin,expected", [("", "https://auth.internal.test"), ("http://localhost:8081", "http://localhost:8081")])
async def test_browser_origin_can_differ_from_verified_internal_auth(settings_stub, public_origin, expected):
    from src.api.routes.auth_sso import sso_login
    from urllib.parse import urlsplit, parse_qs
    settings_stub.AUTH_SERVICE_BASE_URL = "https://auth.internal.test"
    settings_stub.AUTH_SERVICE_PUBLIC_BASE_URL = public_origin
    settings_stub.SSO_REDIRECT_URI = "http://localhost:8000/api/auth/sso/callback"
    settings_stub.SAMPARK_PRODUCT_UNIQUE_ID = "local-product"
    request = fastapi.Request({"type":"http", "method":"GET", "path":"/api/auth/sso/login", "headers":[], "query_string":b"", "session":{}})
    response = await sso_login(request, return_to="/history?tab=practice")
    assert request.session["sso_destination"] == "/history?tab=practice"
    location = response.headers["location"]
    assert location.startswith(expected + "/barabari-auth/")
    assert parse_qs(urlsplit(location).query)["state"] == [request.session["sso_state"]]
    assert settings_stub.AUTH_SERVICE_BASE_URL == "https://auth.internal.test"
