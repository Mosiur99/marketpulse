from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

from marketpulse.docs.fetcher import DocSource


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

    docs_repo: str = "binance/binance-spot-api-docs"
    docs_branch: str = "master"
    docs_files: list[str] = Field(
        default=[
            "rest-api.md",
            "web-socket-streams.md",
            "user-data-stream.md",
            "errors.md",
            "filters.md",
            "enums.md",
        ],
        min_length=1,
    )
    docs_fetch_delay_seconds: float = Field(default=1.0, ge=0, le=60)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None

    def doc_sources(self) -> list[DocSource]:
        return [
            DocSource(
                raw_url=f"https://raw.githubusercontent.com/{self.docs_repo}/{self.docs_branch}/{name}",
                page_url=f"https://github.com/{self.docs_repo}/blob/{self.docs_branch}/{name}",
            )
            for name in self.docs_files
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
