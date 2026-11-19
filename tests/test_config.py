import pytest
from pydantic import ValidationError

from marketpulse.config import Settings


def test_reads_settings_from_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETPULSE_DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/mp")
    monkeypatch.setenv("MARKETPULSE_DATABASE_POOL_SIZE", "7")

    settings = Settings(_env_file=None)

    assert str(settings.database_url) == "postgresql+asyncpg://u:p@db:5432/mp"
    assert settings.database_pool_size == 7


def test_defaults_point_at_the_local_compose_database() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url.hosts()[0]["port"] == 5433
    assert settings.log_level == "INFO"


def test_rejects_a_database_url_that_is_not_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETPULSE_DATABASE_URL", "mysql://u:p@db/mp")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_rejects_a_pool_size_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETPULSE_DATABASE_POOL_SIZE", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
