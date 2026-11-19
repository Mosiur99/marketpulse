from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration read from environment variables prefixed MARKETPULSE_ or a .env file."""

    model_config = SettingsConfigDict(env_prefix="MARKETPULSE_", env_file=".env", extra="ignore")

    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+asyncpg://marketpulse:marketpulse@localhost:5433/marketpulse"
        )
    )
    database_pool_size: int = Field(default=5, ge=1, le=50)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
