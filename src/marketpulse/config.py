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

    ingest_enabled: bool = True
    feed_urls: list[str] = Field(
        default=["wss://data-stream.binance.vision", "wss://stream.binance.com:9443"],
        min_length=1,
    )
    symbols: list[str] = Field(default=["BTCUSDT", "ETHUSDT", "SOLUSDT"], min_length=1)
    ingest_batch_size: int = Field(default=500, ge=1, le=5_000)
    ingest_flush_seconds: float = Field(default=1.0, gt=0, le=60)


@lru_cache
def get_settings() -> Settings:
    return Settings()
