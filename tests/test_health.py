from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from marketpulse.main import create_app


@pytest.fixture
async def client_without_database() -> AsyncIterator[AsyncClient]:
    # Port 1 refuses connections at once, so the database check fails fast.
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    app = create_app(engine=engine)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    await engine.dispose()


async def test_health_reports_a_missing_database_as_degraded(
    client_without_database: AsyncClient,
) -> None:
    response = await client_without_database.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "down"}


async def test_openapi_lists_the_health_endpoint(client_without_database: AsyncClient) -> None:
    response = await client_without_database.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
