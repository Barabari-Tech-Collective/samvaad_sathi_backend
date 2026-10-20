from functools import lru_cache

import decouple

from src.config.settings.base import BackendBaseSettings
from src.config.settings.development import BackendDevSettings
from src.config.settings.environment import Environment
from src.config.settings.production import BackendProdSettings
from src.config.settings.staging import BackendStageSettings


class BackendSettingsFactory:
    def __init__(self, environment: str):
        aliases = {
            "DEV": Environment.DEVELOPMENT.value,
            "DEVELOPMENT": Environment.DEVELOPMENT.value,
            "STAGE": Environment.STAGING.value,
            "STAGING": Environment.STAGING.value,
            "PROD": Environment.PRODUCTION.value,
            "PRODUCTION": Environment.PRODUCTION.value,
        }
        if (
            not isinstance(environment, str)
            or environment.strip().upper() not in aliases
        ):
            raise ValueError(
                "ENVIRONMENT must be DEV, STAGE or PROD (long names are also accepted)."
            )
        self.environment = aliases[environment.strip().upper()]

    def __call__(self) -> BackendBaseSettings:
        if self.environment == Environment.DEVELOPMENT.value:
            return BackendDevSettings(ENVIRONMENT=Environment.DEVELOPMENT)
        elif self.environment == Environment.STAGING.value:
            return BackendStageSettings(ENVIRONMENT=Environment.STAGING)
        elif self.environment == Environment.PRODUCTION.value:
            return BackendProdSettings(ENVIRONMENT=Environment.PRODUCTION)
        raise ValueError("Unknown environment; refusing implicit production settings.")


@lru_cache()
def get_settings() -> BackendBaseSettings:
    return BackendSettingsFactory(environment=decouple.config("ENVIRONMENT", default="DEV", cast=str))()  # type: ignore


settings: BackendBaseSettings = get_settings()
