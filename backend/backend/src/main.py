import logging
import traceback

import fastapi
import fastapi.responses
import uvicorn
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.api.endpoints import router as api_endpoint_router
from src.config.events import (
    execute_backend_server_event_handler,
    terminate_backend_server_event_handler,
)
from src.config.loki_logging import configure_loki_logging
from src.config.manager import settings
from src.config.settings.environment import Environment

logger = logging.getLogger(__name__)


def verify_sso_configuration() -> None:
    """Refuse to serve a deployment that cannot complete a student sign-in.

    An empty AUTH_SERVICE_BASE_URL makes the authorize redirect a relative path
    that resolves back to this service and answers 404, so every student sees a
    dead "Continue" button with nothing in the logs explaining why. A deployed
    environment therefore fails to start rather than serving a broken login.

    Local development is still allowed to run without the auth service, because
    most work does not touch sign-in; the SSO routes report a clear error there
    instead of emitting an unusable redirect.
    """
    if settings.AUTH_SERVICE_BASE_URL.strip():
        return
    message = (
        "AUTH_SERVICE_BASE_URL is not set, so Sampark Saathi SSO cannot build a "
        "valid authorize redirect and student sign-in would fail."
    )
    if settings.ENVIRONMENT != Environment.DEVELOPMENT.value:
        raise RuntimeError(message)
    logger.error(
        "%s Continuing because this is local development; /api/auth/sso routes "
        "will report that sign-in is unavailable.",
        message,
    )


def verify_central_auth_configuration() -> None:
    """Fail at startup, naming what is missing, when central authentication cannot work.

    Central authentication is the only way in, so without its settings every request would
    answer 503 and the cause would only show up one request at a time. This is keyed on an
    explicit setting rather than ENVIRONMENT, because a deployed host may run with
    ENVIRONMENT=DEV. Local work and tests opt out with REQUIRE_CENTRAL_AUTH_CONFIG=False.
    """
    if not getattr(settings, "CENTRAL_IDENTITY_CHECK_ENABLED", False):
        return
    if not getattr(settings, "REQUIRE_CENTRAL_AUTH_CONFIG", True):
        return
    missing = []
    base = str(getattr(settings, "AUTH_SERVICE_BASE_URL", "") or "").strip()
    if not base.startswith("https://"):
        missing.append("AUTH_SERVICE_BASE_URL (an https:// origin)")
    if not str(getattr(settings, "PRODUCT_IDENTITY_API_KEY", "") or "").strip():
        missing.append("PRODUCT_IDENTITY_API_KEY")
    if not str(getattr(settings, "SAMPARK_PRODUCT_UNIQUE_ID", "") or "").strip():
        missing.append("SAMPARK_PRODUCT_UNIQUE_ID")
    if missing:
        raise RuntimeError(
            "Central authentication is on but not configured: set "
            + ", ".join(missing)
            + ". Without them every request is refused. For local work only, set "
            "REQUIRE_CENTRAL_AUTH_CONFIG=False."
        )


def initialize_backend_application() -> fastapi.FastAPI:

    logging.basicConfig(
        level=settings.LOGGING_LEVEL,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )

     # Configure application logging
    logging.getLogger().setLevel(settings.LOGGING_LEVEL)
    logging.getLogger("src").setLevel(settings.LOGGING_LEVEL)
    configure_loki_logging()

    # Load environment variables from .env if present
    load_dotenv()
    verify_sso_configuration()
    verify_central_auth_configuration()
    app = fastapi.FastAPI(**settings.set_backend_app_attributes)  # type: ignore

    # Tags metadata for Swagger grouping
    tags_metadata = [
        {
            "name": "users",
            "description": "User registration, login, and authentication.",
        },
        {
            "name": "resume",
            "description": "Resume upload, parsing, and profile enrichment.",
        },
        {
            "name": "interviews",
            "description": "Interview creation, management, and question flow.",
        },
        {"name": "audio", "description": "Audio upload and Whisper transcription."},
        {
            "name": "analysis",
            "description": "Domain/communication analysis and pacing/pauses metrics.",
        },
        {
            "name": "report",
            "description": "Session-level final report generation and retrieval.",
        },
        {
            "name": "analytics",
            "description": "Dashboard-focused analytics across student, interview, segment, system, scoring, and alerts views.",
        },
    ]
    # Attach tag descriptions to OpenAPI
    app.openapi_tags = tags_metadata  # type: ignore[attr-defined]

    # Optionally refine title/description at runtime without touching env
    try:
        app.title = "Samvaad Sathi Backend API"
        if not getattr(settings, "DESCRIPTION", None):
            app.description = "APIs for AI-driven interview practice: resume parsing, question generation, audio transcription, analyses, and reports."
    except Exception:
        # Non-fatal if attributes are not available
        pass

    # Registered before CORSMiddleware so that it ends up *inside* it. Starlette
    # turns an unhandled exception into a 500 at ServerErrorMiddleware, which sits
    # outside the CORS layer, so that response carries no Access-Control-Allow-Origin
    # and the browser reports "blocked by CORS policy" instead of the real failure.
    # Every server error then looks like a CORS misconfiguration from the frontend,
    # which is a long detour away from the actual bug. Turning the exception into a
    # response here means CORSMiddleware still adds its headers and the browser can
    # read the status.
    @app.middleware("http")
    async def return_unhandled_errors_with_cors_headers(request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled error | %s %s", request.method, request.url.path
            )
            # Gated on its own opt-in setting rather than on ENVIRONMENT. The
            # production host runs with ENVIRONMENT=DEV, so keying this on the
            # environment would have echoed exception text - table names, driver
            # errors - straight to students' browsers.
            detail = "Something went wrong. Please try again."
            if settings.EXPOSE_ERROR_DETAIL:
                detail = f"{detail} (detail: {traceback.format_exc(limit=0).strip()})"
            return fastapi.responses.JSONResponse(status_code=500, content={"detail": detail})

    # Add middleware BEFORE including routers (middleware is applied in reverse order)
    # CORS middleware should be added first to handle preflight requests
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=settings.IS_ALLOWED_CREDENTIALS,
        allow_methods=settings.ALLOWED_METHODS,
        allow_headers=settings.ALLOWED_HEADERS,
    )

    # Enable server-side sessions for OAuth state and userinfo storage
    # Uses cookie-based signed session via Starlette's SessionMiddleware
    app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)

    # Honour X-Forwarded-Proto/-For from the TLS-terminating proxy in front of this app
    # (Render, or nginx on EC2). Without it request.url/request.url_for() report http://
    # even on an https request, which breaks any absolute URL this service generates.
    # uvicorn can do this itself, but only when launched with --forwarded-allow-ips;
    # its default is "127.0.0.1" and neither Render's proxy nor nginx is on loopback,
    # so the headers are silently dropped. Applying it here makes correctness
    # independent of how the process happens to be started (Docker CMD, systemd unit,
    # or a start command typed into a hosting dashboard).
    # trusted_hosts="*" is appropriate because the only route to this app is through
    # that proxy; it is not directly reachable from the internet.
    if settings.TRUST_PROXY_HEADERS:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    # Step timings for finding slow requests on staging; never on unless asked for.
    if settings.SERVER_TIMING_ENABLED:
        from src.utilities import server_timing

        server_timing.install(app)

    startup_handler = execute_backend_server_event_handler(backend_app=app)
    shutdown_handler = terminate_backend_server_event_handler(backend_app=app)
    if hasattr(app, "add_event_handler"):
        app.add_event_handler("startup", startup_handler)
        app.add_event_handler("shutdown", shutdown_handler)
    else:
        app.on_event("startup")(startup_handler)
        app.on_event("shutdown")(shutdown_handler)

    app.include_router(router=api_endpoint_router, prefix=settings.API_PREFIX)

    # Add a root endpoint
    @app.get("/")
    async def root():
        return {
            "message": "Welcome to Samvaad Sathi Backend API",
            "version": settings.VERSION,
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


backend_app: fastapi.FastAPI = initialize_backend_application()

if __name__ == "__main__":
    uvicorn.run(
        app="src.main:backend_app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=settings.DEBUG,
        workers=settings.SERVER_WORKERS,
        log_level=settings.LOGGING_LEVEL,
    )
