from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer

PGVECTOR_IMAGE = "pgvector/pgvector:pg17"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer(PGVECTOR_IMAGE, driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        config.attributes["configure_logger"] = False
        command.upgrade(config, "head")
        yield url


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()
