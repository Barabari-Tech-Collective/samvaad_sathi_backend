import logging
import pathlib

import decouple
import pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR: pathlib.Path = pathlib.Path(__file__).parent.parent.parent.parent.parent.resolve()


class BackendBaseSettings(BaseSettings):
    TITLE: str = "DAPSQL FARN-Stack Template Application"
    VERSION: str = "0.1.0"
    TIMEZONE: str = "UTC"
    DESCRIPTION: str | None = None
    DEBUG: bool = False
    ENVIRONMENT: str = "DEV"  # Default environment, overridden by subclasses

    SERVER_HOST: str = decouple.config("BACKEND_SERVER_HOST", cast=str)  # type: ignore
    SERVER_PORT: int = decouple.config("BACKEND_SERVER_PORT", cast=int)  # type: ignore
    SERVER_WORKERS: int = decouple.config("BACKEND_SERVER_WORKERS", cast=int)  # type: ignore
    API_PREFIX: str = "/api"
    # Interactive docs are served unauthenticated, so they hand anyone who
    # finds the host a complete map of the API surface. Kept on by default for
    # local/dev convenience; set EXPOSE_API_DOCS=False in any internet-facing
    # environment (see set_backend_app_attributes, which nulls these out).
    EXPOSE_API_DOCS: bool = decouple.config("EXPOSE_API_DOCS", cast=bool, default=True)  # type: ignore

    # Comma-separated emails granted access to the cross-student analytics /
    # dashboard endpoints, in addition to anyone with user.is_admin set in the
    # database. Exists because the DB flag defaults to false for every existing
    # row and flipping it needs SQL access, which whoever configures this
    # service may not have. Empty by default: no student is ever included
    # implicitly. See api/dependencies/admin.py.
    ADMIN_EMAILS: str = decouple.config("ADMIN_EMAILS", cast=str, default="")  # type: ignore
    DOCS_URL: str = "/docs"
    OPENAPI_URL: str = "/openapi.json"
    REDOC_URL: str = "/redoc"
    OPENAPI_PREFIX: str = ""

    DB_POSTGRES_HOST: str = decouple.config("POSTGRES_HOST", cast=str)  # type: ignore
    DB_MAX_POOL_CON: int = decouple.config("DB_MAX_POOL_CON", cast=int)  # type: ignore
    DB_POSTGRES_NAME: str = decouple.config("POSTGRES_DB", cast=str)  # type: ignore
    DB_POSTGRES_PASSWORD: str = decouple.config("POSTGRES_PASSWORD", cast=str)  # type: ignore
    DB_POOL_SIZE: int = decouple.config("DB_POOL_SIZE", cast=int)  # type: ignore
    DB_POOL_OVERFLOW: int = decouple.config("DB_POOL_OVERFLOW", cast=int)  # type: ignore
    DB_POSTGRES_PORT: int = decouple.config("POSTGRES_PORT", cast=int)  # type: ignore
    DB_POSTGRES_SCHEMA: str = decouple.config("POSTGRES_SCHEMA", cast=str)  # type: ignore
    DB_TIMEOUT: int = decouple.config("DB_TIMEOUT", cast=int)  # type: ignore
    DB_POSTGRES_USERNAME: str = decouple.config("POSTGRES_USERNAME", cast=str)  # type: ignore
    # Managed Postgres providers require TLS, while a developer's local
    # PostgreSQL instance commonly does not. Keep TLS on by default so staging
    # and production remain fail-safe; local setup must opt out explicitly.
    DB_SSL_ENABLED: bool = decouple.config("DB_SSL_ENABLED", cast=bool, default=True)  # type: ignore

    IS_DB_ECHO_LOG: bool = decouple.config("IS_DB_ECHO_LOG", cast=bool)  # type: ignore
    IS_DB_FORCE_ROLLBACK: bool = decouple.config("IS_DB_FORCE_ROLLBACK", cast=bool)  # type: ignore
    IS_DB_EXPIRE_ON_COMMIT: bool = decouple.config("IS_DB_EXPIRE_ON_COMMIT", cast=bool)  # type: ignore

    API_TOKEN: str = decouple.config("API_TOKEN", cast=str)  # type: ignore
    AUTH_TOKEN: str = decouple.config("AUTH_TOKEN", cast=str)  # type: ignore
    JWT_TOKEN_PREFIX: str = decouple.config("JWT_TOKEN_PREFIX", cast=str)  # type: ignore
    JWT_SECRET_KEY: str = decouple.config("JWT_SECRET_KEY", cast=str)  # type: ignore
    JWT_SUBJECT: str = decouple.config("JWT_SUBJECT", cast=str)  # type: ignore
    JWT_MIN: int = decouple.config("JWT_MIN", cast=int)  # type: ignore
    JWT_HOUR: int = decouple.config("JWT_HOUR", cast=int)  # type: ignore
    JWT_DAY: int = decouple.config("JWT_DAY", cast=int)  # type: ignore
    JWT_ACCESS_TOKEN_EXPIRATION_TIME: int = JWT_MIN * JWT_HOUR * JWT_DAY

    IS_ALLOWED_CREDENTIALS: bool = decouple.config("IS_ALLOWED_CREDENTIALS", cast=bool)  # type: ignore
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",  # React default port
        "http://localhost:3001",
        "http://0.0.0.0:3000",
        "http://127.0.0.1:3000",  # React docker port
        "http://127.0.0.1:3001",
        "http://localhost:5173",  # Qwik default port
        "http://localhost:5174",
        "http://0.0.0.0:5173",
        "http://127.0.0.1:5173",  # Qwik docker port
        "http://127.0.0.1:5174",
        "https://backend-samvaad-saathi.barabaricollective.org",
        "https://samvaad-saathi-frontend.vercel.app" #vercel new prod
        "https://samvaad-saathi-frontend.vercel.app", #vercel new prod
        "https://samvaad-sathi.barabaricollective.org",  # Production frontend (without www)
        "https://www.samvaad-sathi.barabaricollective.org",  # Production frontend (with www)
        "https://dev-backend-samvaadsathi.barabaricollective.org",  # Dev backend (for local frontend testing)
        "https://samvaad-saathi-dashboard.onrender.com",  # Dashboard frontend on Render
        "https://samvaad-dashboard.barabaricollective.org",
        "https://samvaad-saathi.barabaricollective.org", #new domain for production
        "https://master.d30wpikvvj1kc2.amplifyapp.com",
        "https://main.d36hlrn367i0rr.amplifyapp.com", #new aws production
        "https://master.d1ljdkppy5vau1.amplifyapp.com", #new aws dashboard prod
        # staging
        "https://samvaad-saathi-staging.barabaricollective.org",
        "https://api-staging.barabaricollective.org",
        # Staging on render
        "https://samvaad-sathi-backend-tdd0.onrender.com",
        "https://samvaad-saathi-frontend.onrender.com",
    ]
    ALLOWED_METHODS: list[str] = ["*"]
    ALLOWED_HEADERS: list[str] = ["*"]

    LOGGING_LEVEL: int = logging.INFO
    LOGGERS: tuple[str, str] = ("uvicorn.asgi", "uvicorn.access")

    HASHING_ALGORITHM_LAYER_1: str = decouple.config("HASHING_ALGORITHM_LAYER_1", cast=str)  # type: ignore
    HASHING_ALGORITHM_LAYER_2: str = decouple.config("HASHING_ALGORITHM_LAYER_2", cast=str)  # type: ignore
    HASHING_SALT: str = decouple.config("HASHING_SALT", cast=str)  # type: ignore
    JWT_ALGORITHM: str = decouple.config("JWT_ALGORITHM", cast=str)  # type: ignore

    # ------------------------------
    # Sessions / OAuth (Cognito)
    # ------------------------------
    SESSION_SECRET_KEY: str = decouple.config("SESSION_SECRET_KEY", cast=str)  # type: ignore
    COGNITO_REGION: str = decouple.config("COGNITO_REGION", cast=str, default="ap-south-1")  # type: ignore
    COGNITO_USERPOOL_ID: str = decouple.config("COGNITO_USERPOOL_ID", cast=str, default="")  # type: ignore
    COGNITO_CLIENT_ID: str = decouple.config("COGNITO_CLIENT_ID", cast=str, default="")  # type: ignore
    COGNITO_CLIENT_SECRET: str = decouple.config("COGNITO_CLIENT_SECRET", cast=str, default="")  # type: ignore
    COGNITO_SCOPES: str = decouple.config("COGNITO_SCOPES", cast=str, default="openid email phone profile")  # type: ignore
    # Optional hosted UI domain like: your-domain.auth.ap-south-1.amazoncognito.com (omit protocol)
    COGNITO_HOSTED_UI_DOMAIN: str | None = decouple.config("COGNITO_HOSTED_UI_DOMAIN", cast=str, default=None)  # type: ignore
    # Optional frontend redirect after successful login/logout
    COGNITO_POST_LOGIN_REDIRECT_URL: str | None = decouple.config("COGNITO_POST_LOGIN_REDIRECT_URL", cast=str, default=None)  # type: ignore
    COGNITO_POST_LOGOUT_REDIRECT_URL: str | None = decouple.config("COGNITO_POST_LOGOUT_REDIRECT_URL", cast=str, default=None)  # type: ignore

    # Refresh token settings (in minutes). Default: 30 days
    REFRESH_TOKEN_EXPIRY_MINUTES: int = decouple.config("REFRESH_TOKEN_EXPIRY_MINUTES", cast=int, default=60 * 24 * 30)  # type: ignore

    # ------------------------------
    # Sampark Saathi central auth (replaces Cognito as the source of student identity)
    # ------------------------------
    # Public origin of the barabari-auth-service, e.g. https://barabari-auth-service.onrender.com
    AUTH_SERVICE_BASE_URL: str = decouple.config("AUTH_SERVICE_BASE_URL", cast=str, default="")  # type: ignore
    # Same value as auth-service's JWT_SECRET env var (a Base64 string decoded to raw HMAC key
    # bytes on both sides - see src/securities/authorizations/sso_jwt.py). Shares auth-service's
    # own local-dev default so both services validate each other's tokens out of the box.
    AUTH_SERVICE_JWT_SECRET: str = decouple.config(
        "AUTH_SERVICE_JWT_SECRET",
        cast=str,
        default="",
    )  # type: ignore
    AUTH_SERVICE_STAGING_JWT_SECRET: str = decouple.config(
        "AUTH_SERVICE_STAGING_JWT_SECRET",
        cast=str,
        default="",
    )  # type: ignore
    # This product's row in auth-service's `products` table (already seeded as SAMVAAD_SAATHI).
    SAMPARK_PRODUCT_UNIQUE_ID: str = decouple.config(
        "SAMPARK_PRODUCT_UNIQUE_ID", cast=str, default="81c53f68-35f4-4133-9e35-06f5c30354b785"
    )  # type: ignore
    # The exact callback URL registered in auth-service's SSO_ALLOWED_REDIRECT_URIS.
    # auth-service compares this by exact string, so it must NOT be derived from the
    # incoming request: behind a TLS-terminating proxy (Render, nginx) request.url_for()
    # yields http:// unless uvicorn is told to trust X-Forwarded-Proto, which silently
    # produces a value that will never match the registered https:// entry. Set this
    # explicitly per environment; it falls back to url_for() only for local dev.
    SSO_REDIRECT_URI: str = decouple.config("SSO_REDIRECT_URI", cast=str, default="")  # type: ignore
    # auth-service issues one token type across every Barabari product using a shared
    # JWT secret, and the token carries no audience/product claim. Samvaad Saathi accepts
    # a comma-separated list of roles to allow students in, as well as local admins 
    # to access their respective endpoints. Empty disables the check.
    SSO_REQUIRED_ROLE: str = decouple.config("SSO_REQUIRED_ROLE", cast=str, default="STUDENT,ADMIN,SUPER_ADMIN")  # type: ignore

    # Trust X-Forwarded-Proto/-For from the reverse proxy in front of this app. Correct
    # for Render and for nginx on EC2, where the proxy is the only way in. Set False
    # only if this process is ever exposed directly to the internet, where a client
    # could forge those headers.
    TRUST_PROXY_HEADERS: bool = decouple.config("TRUST_PROXY_HEADERS", cast=bool, default=True)  # type: ignore

    # ------------------------------
    # Central-auth entitlement revocation (Phase 6, part B)
    # ------------------------------
    # auth-service's /authorize and /token only check entitlement at login/token-issuance
    # time - an already-issued access token keeps working for its own lifetime regardless
    # of a later revoke, since verification here never calls back to auth-service. This
    # consumes auth-service's USER_ACCESS_REVOKED_EVENT broadcast (RabbitMQ, same bridge
    # already used for STUDENT_CREATED_EVENT/STUDENT_PROFILE_SAVED_EVENT) to close that
    # window without adding a synchronous cross-service call to every request - see
    # src/mqconsumer/user_access_revoked_consumer.py and get_current_user's revoked check.
    # False disables the consumer entirely (e.g. local dev without RabbitMQ running);
    # matches Redis's own fail-open-at-startup behavior - a stopped consumer just means
    # revocation isn't enforced, not that the app fails to start.
    RABBITMQ_ENABLED: bool = decouple.config("RABBITMQ_ENABLED", cast=bool, default=False)  # type: ignore
    RABBITMQ_HOST: str = decouple.config("RABBITMQ_HOST", cast=str, default="localhost")  # type: ignore
    RABBITMQ_PORT: int = decouple.config("RABBITMQ_PORT", cast=int, default=5672)  # type: ignore
    RABBITMQ_USERNAME: str = decouple.config("RABBITMQ_USERNAME", cast=str, default="guest")  # type: ignore
    RABBITMQ_PASSWORD: str = decouple.config("RABBITMQ_PASSWORD", cast=str, default="guest")  # type: ignore
    RABBITMQ_VIRTUAL_HOST: str = decouple.config("RABBITMQ_VIRTUAL_HOST", cast=str, default="/")  # type: ignore
    RABBITMQ_SSL_ENABLED: bool = decouple.config("RABBITMQ_SSL_ENABLED", cast=bool, default=False)  # type: ignore
    # Must match auth-service's env.rabbitQueues.userAccessRevoked.exchange exactly - a
    # topic exchange auth-service publishes to and every consuming product (this one now,
    # others later) binds its own queue to independently. Defaults to the prod name; local
    # dev against a local auth-service should override to the "-local"-suffixed one.
    RABBITMQ_ACCESS_REVOKED_EXCHANGE: str = decouple.config(
        "RABBITMQ_ACCESS_REVOKED_EXCHANGE", cast=str, default="barabari.user-access-revoked-exchange"
    )  # type: ignore
    RABBITMQ_ACCESS_REVOKED_ROUTING_KEY: str = decouple.config(
        "RABBITMQ_ACCESS_REVOKED_ROUTING_KEY", cast=str, default="barabari.user-access-revoked-routing-key"
    )  # type: ignore
    # This product's own durable queue name, bound to the exchange above. Distinct from
    # any other product's queue name so a topic-exchange fan-out reaches every consumer
    # independently rather than round-robin-splitting one shared queue between them.
    RABBITMQ_ACCESS_REVOKED_QUEUE: str = decouple.config(
        "RABBITMQ_ACCESS_REVOKED_QUEUE", cast=str, default="barabari.samvaad-saathi.access-revoked-queue"
    )  # type: ignore
    # How long a revoked-flag lives in Redis once set. Deliberately longer than
    # auth-service's own access-token lifetime (default 1 hour) rather than trying to
    # match it exactly - erring long only costs a little unused Redis memory, erring short
    # would let a revoked user regain silent access before their old token actually
    # expires.
    ACCESS_REVOKED_FLAG_TTL_SECONDS: int = decouple.config(
        "ACCESS_REVOKED_FLAG_TTL_SECONDS", cast=int, default=60 * 60 * 24
    )  # type: ignore

    # Audio processing settings (stateless - no upload directory needed)
    MAX_AUDIO_SIZE_MB: int = decouple.config("MAX_AUDIO_SIZE_MB", cast=int, default=25)  # type: ignore
    OPENAI_MODEL: str = decouple.config("OPENAI_MODEL", cast=str, default="gpt-4o-mini")  # type: ignore
    OPENAI_API_KEY: str = decouple.config("OPENAI_API_KEY", cast=str, default="")  # type: ignore
    # LLM/ OpenAI client timeout in seconds (request-level). Increase for longer prompts/outputs.
    OPENAI_TIMEOUT_SECONDS: float = decouple.config("OPENAI_TIMEOUT_SECONDS", cast=float, default=150.0)  # type: ignore

    # Which provider src/services/llm.py's structured_output() calls actually use.
    # "openai" (default, unchanged behavior) or "deepseek". Whisper (whisper.py) and
    # TTS (pronunciation_tts.py, elevenlabs_tts.py) are NOT affected by this - neither
    # DeepSeek nor this setting apply to those; see the scaling plan's Phase 7/8 split.
    LLM_PROVIDER: str = decouple.config("LLM_PROVIDER", cast=str, default="openai")  # type: ignore
    DEEPSEEK_API_KEY: str = decouple.config("DEEPSEEK_API_KEY", cast=str, default="")  # type: ignore
    DEEPSEEK_BASE_URL: str = decouple.config("DEEPSEEK_BASE_URL", cast=str, default="https://api.deepseek.com")  # type: ignore
    DEEPSEEK_MODEL: str = decouple.config("DEEPSEEK_MODEL", cast=str, default="deepseek-v4-flash")  # type: ignore

    # Which provider src/services/whisper.py's transcribe_audio_with_whisper()
    # actually uses. "groq" (default, as of 2026-09-11 - live-verified against
    # near word-perfect transcription on real synthesized audio, ~89% cheaper
    # than OpenAI) or "openai" (kept as a fallback path, not deleted, in case
    # Groq has an outage or broader real-audio testing turns up quality
    # issues - switching back is a one-var change, not a redeploy of new
    # code). See scaling plan Phase 8. Separate from LLM_PROVIDER; DeepSeek
    # has no transcription capability at all, so this is a different provider.
    STT_PROVIDER: str = decouple.config("STT_PROVIDER", cast=str, default="groq")  # type: ignore
    GROQ_API_KEY: str = decouple.config("GROQ_API_KEY", cast=str, default="")  # type: ignore
    GROQ_BASE_URL: str = decouple.config("GROQ_BASE_URL", cast=str, default="https://api.groq.com/openai/v1")  # type: ignore
    GROQ_WHISPER_MODEL: str = decouple.config("GROQ_WHISPER_MODEL", cast=str, default="whisper-large-v3-turbo")  # type: ignore

    # ElevenLabs TTS
    ELEVENLABS_API_KEY: str = decouple.config("ELEVENLABS_API_KEY", cast=str, default="")  # type: ignore
    ELEVENLABS_VOICE_ID: str = decouple.config("ELEVENLABS_VOICE_ID", cast=str, default="hpp4J3VqNfWAUOO0d1Us")  # type: ignore

    # Toggle to bypass expensive TTS APIs (ElevenLabs and OpenAI TTS) in staging
    MOCK_TTS: bool = decouple.config("MOCK_TTS", cast=bool, default=False)  # type: ignore

    # Barabari Sampark Saathi resume-round callback
    SAMPARK_SAATHI_API_KEY: str = decouple.config("SAMPARK_SAATHI_API_KEY", cast=str, default="")  # type: ignore
    SAMPARK_SAATHI_BASE_URL: str = decouple.config("SAMPARK_SAATHI_BASE_URL", cast=str, default="")  # type: ignore
    # Auth-service Central Student Registry. Product backends use this server-side key to
    # hydrate common profile fields; it must match auth-service CENTRAL_PROFILE_API_KEY.
    CENTRAL_PROFILE_API_KEY: str = decouple.config("CENTRAL_PROFILE_API_KEY", cast=str, default="")  # type: ignore

    # Super-admin plan, Phase 12: shared secret for the *inbound* direction (auth-service's
    # SUPER_ADMIN panel calling into this service to designate a Samvaad Saathi admin) - a
    # new, dedicated trust relationship, not a reuse of SAMPARK_SAATHI_API_KEY above (which
    # is for a different pair of services and a different direction). Empty by default,
    # which the endpoint treats as "disabled" rather than "open" - see internal_admin.py.
    SUPER_ADMIN_API_KEY: str = decouple.config("SUPER_ADMIN_API_KEY", cast=str, default="")  # type: ignore

    # ------------------------------
    # Grafana Cloud Loki (free tier) - Super-admin plan, Phase 13. Optional: unset in local
    # dev, logging_config.py only attaches the Loki handler when LOKI_URL is present.
    # ------------------------------
    LOKI_URL: str = decouple.config("LOKI_URL", cast=str, default="")  # type: ignore
    LOKI_USERNAME: str = decouple.config("LOKI_USERNAME", cast=str, default="")  # type: ignore
    LOKI_API_KEY: str = decouple.config("LOKI_API_KEY", cast=str, default="")  # type: ignore

    # ------------------------------
    # Redis (self-hosted on the same EC2 instance, no managed service)
    # ------------------------------
    REDIS_HOST: str = decouple.config("REDIS_HOST", cast=str, default="localhost")  # type: ignore
    REDIS_PORT: int = decouple.config("REDIS_PORT", cast=int, default=6379)  # type: ignore
    REDIS_DB: int = decouple.config("REDIS_DB", cast=int, default=0)  # type: ignore
    REDIS_PASSWORD: str = decouple.config("REDIS_PASSWORD", cast=str, default="")  # type: ignore

    # Per-user rate limits on endpoints calling metered third-party APIs
    # (OpenAI/ElevenLabs) - protects the API bill from runaway usage by a
    # single user or a client-side bug, not primarily a security control.
    RATE_LIMIT_TTS_PER_MINUTE: int = decouple.config("RATE_LIMIT_TTS_PER_MINUTE", cast=int, default=20)  # type: ignore
    RATE_LIMIT_RESUME_ANALYSIS_PER_HOUR: int = decouple.config("RATE_LIMIT_RESUME_ANALYSIS_PER_HOUR", cast=int, default=10)  # type: ignore

    # Per-IP throttles on unauthenticated auth endpoints. These previously had
    # no rate limiting at all, which combined with an unbounded password field
    # left credential stuffing completely unthrottled.
    RATE_LIMIT_LOGIN_PER_MINUTE: int = decouple.config("RATE_LIMIT_LOGIN_PER_MINUTE", cast=int, default=10)  # type: ignore
    RATE_LIMIT_SIGNUP_PER_HOUR: int = decouple.config("RATE_LIMIT_SIGNUP_PER_HOUR", cast=int, default=20)  # type: ignore

    # Minimum password length enforced at registration.
    MIN_PASSWORD_LENGTH: int = decouple.config("MIN_PASSWORD_LENGTH", cast=int, default=8)  # type: ignore

    # How long cached TTS audio for identical (text, voice_id) pairs is kept.
    # Interview questions repeat heavily across users, so caching cuts both
    # ElevenLabs cost and per-request latency on cache hits.
    TTS_CACHE_TTL_SECONDS: int = decouple.config("TTS_CACHE_TTL_SECONDS", cast=int, default=60 * 60 * 24 * 30)  # type: ignore

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=f"{str(ROOT_DIR)}/.env",
        validate_assignment=True,
        extra='allow'
    )

    @property
    def set_backend_app_attributes(self) -> dict[str, str | bool | None]:
        """
        Set all `FastAPI` class' attributes with the custom values defined in `BackendBaseSettings`.
        """
        # Passing None disables the route entirely in FastAPI, which is what we
        # want when docs are not meant to be public - serving a 404 rather than
        # the full endpoint inventory.
        return {
            "title": self.TITLE,
            "version": self.VERSION,
            "debug": self.DEBUG,
            "description": self.DESCRIPTION,
            "docs_url": self.DOCS_URL if self.EXPOSE_API_DOCS else None,
            "openapi_url": self.OPENAPI_URL if self.EXPOSE_API_DOCS else None,
            "redoc_url": self.REDOC_URL if self.EXPOSE_API_DOCS else None,
            "openapi_prefix": self.OPENAPI_PREFIX,
            "api_prefix": self.API_PREFIX,
        }
