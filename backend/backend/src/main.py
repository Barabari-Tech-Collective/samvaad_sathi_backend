import logging
import fastapi
import uvicorn
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.api.endpoints import router as api_endpoint_router
from src.config.events import (
    execute_backend_server_event_handler,
    terminate_backend_server_event_handler,
)
from src.config.manager import settings


def initialize_backend_application() -> fastapi.FastAPI:
    
    logging.basicConfig(
        level=settings.LOGGING_LEVEL,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )

     # Configure application logging
    logging.getLogger().setLevel(settings.LOGGING_LEVEL)
    logging.getLogger("src").setLevel(settings.LOGGING_LEVEL)

    logging.getLogger(__name__).info(
    "######## APPLICATION INFO LOG TEST ########"
    )
    logging.getLogger(__name__).warning(
    "######## APPLICATION WARNING LOG TEST ########"
    )

    # Load environment variables from .env if present
    load_dotenv()
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
