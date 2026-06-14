import logging
import pathlib

import decouple
import pydantic
from pydantic_settings import BaseSettings

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
        "https://samvaad-sathi.barabaricollective.org",  # Production frontend (without www)
        "https://www.samvaad-sathi.barabaricollective.org",  # Production frontend (with www)
        "https://dev-backend-samvaadsathi.barabaricollective.org",  # Dev backend (for local frontend testing)
        "https://samvaad-dashboard.barabaricollective.org",
        "https://master.d30wpikvvj1kc2.amplifyapp.com"
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
    SESSION_SECRET_KEY: str = decouple.config("SESSION_SECRET_KEY", cast=str, default="change-me-session-secret")  # type: ignore
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

    # Audio processing settings (stateless - no upload directory needed)
    MAX_AUDIO_SIZE_MB: int = decouple.config("MAX_AUDIO_SIZE_MB", cast=int, default=25)  # type: ignore
    OPENAI_MODEL: str = decouple.config("OPENAI_MODEL", cast=str, default="gpt-4o-mini")  # type: ignore
    OPENAI_API_KEY: str = decouple.config("OPENAI_API_KEY", cast=str, default="")  # type: ignore
    # LLM/ OpenAI client timeout in seconds (request-level). Increase for longer prompts/outputs.
    OPENAI_TIMEOUT_SECONDS: float = decouple.config("OPENAI_TIMEOUT_SECONDS", cast=float, default=150.0)  # type: ignore

    # ElevenLabs TTS
    ELEVENLABS_API_KEY: str = decouple.config("ELEVENLABS_API_KEY", cast=str, default="")  # type: ignore
    ELEVENLABS_VOICE_ID: str = decouple.config("ELEVENLABS_VOICE_ID", cast=str, default="hpp4J3VqNfWAUOO0d1Us")  # type: ignore

    model_config = pydantic.ConfigDict(
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
        return {
            "title": self.TITLE,
            "version": self.VERSION,
            "debug": self.DEBUG,
            "description": self.DESCRIPTION,
            "docs_url": self.DOCS_URL,
            "openapi_url": self.OPENAPI_URL,
            "redoc_url": self.REDOC_URL,
            "openapi_prefix": self.OPENAPI_PREFIX,
            "api_prefix": self.API_PREFIX,
        }
