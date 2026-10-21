from src.config.settings.base import BackendBaseSettings
from src.config.settings.environment import Environment


class BackendStageSettings(BackendBaseSettings):
    DESCRIPTION: str | None = "Isolated staging environment."
    DEBUG: bool = False
    ENVIRONMENT: Environment = Environment.STAGING
