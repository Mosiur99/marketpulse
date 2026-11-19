import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.main import create_app

pytestmark = pytest.mark.integration


async def test_migrations_enable_pgvector(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        version = await connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        distance = await connection.scalar(text("SELECT '[1,0]'::vector <=> '[0,1]'::vector"))

    assert version is not None
    assert distance == pytest.approx(1.0)


async def test_health_reports_a_reachable_database_as_ok(engine: AsyncEngine) -> None:
    app = create_app(engine=engine)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
