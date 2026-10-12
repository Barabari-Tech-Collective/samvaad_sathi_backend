"""A server error must reach the browser as a server error.

Starlette converts an unhandled exception into a 500 at ServerErrorMiddleware,
which sits outside the CORS layer, so the response carried no
Access-Control-Allow-Origin header. The browser then reported "blocked by CORS
policy" and the real failure was invisible from the frontend, sending anyone
debugging it after a CORS misconfiguration that did not exist.
"""

import fastapi
import pytest
from starlette.testclient import TestClient

from src.config.manager import settings
from src.main import initialize_backend_application

ORIGIN = "http://localhost:3000"


@pytest.fixture
def client_with_failing_route():
    app = initialize_backend_application()

    @app.get("/_test_unhandled_failure")
    async def _boom():
        raise RuntimeError("database is on fire")

    # Mirrors a browser: let the error response through instead of re-raising.
    return TestClient(app, raise_server_exceptions=False)


def test_allowed_origin_is_configured_for_the_test():
    assert ORIGIN in settings.ALLOWED_ORIGINS


def test_unhandled_error_returns_500_with_cors_headers(client_with_failing_route):
    response = client_with_failing_route.get(
        "/_test_unhandled_failure", headers={"Origin": ORIGIN}
    )
    assert response.status_code == 500
    # Without this header the browser discards the response and reports a CORS
    # failure, which is what made every server error look like a CORS bug.
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert "detail" in response.json()


def test_successful_response_still_carries_cors_headers(client_with_failing_route):
    """The error path must not have displaced normal CORS handling."""
    response = client_with_failing_route.get("/docs", headers={"Origin": ORIGIN})
    assert response.status_code == fastapi.status.HTTP_200_OK
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_error_detail_is_withheld_unless_explicitly_enabled(client_with_failing_route):
    """A 500 must not describe itself to the caller by default.

    The detail is gated on EXPOSE_ERROR_DETAIL rather than on ENVIRONMENT,
    because the production host runs with ENVIRONMENT=DEV - anything keyed on
    the environment would have leaked driver errors and table names to
    students' browsers.
    """
    assert settings.EXPOSE_ERROR_DETAIL is False, "must default to withholding detail"
    response = client_with_failing_route.get(
        "/_test_unhandled_failure", headers={"Origin": ORIGIN}
    )
    detail = response.json()["detail"]
    assert "database is on fire" not in detail
    assert "RuntimeError" not in detail
    assert "Something went wrong" in detail
