from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from marketpulse.config import Settings
from marketpulse.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An app whose database is unreachable: every case here must fail before any query."""
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    app = create_app(Settings(_env_file=None, ingest_enabled=False), engine=engine)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            http.app = app  # type: ignore[attr-defined]
            yield http
    await engine.dispose()


async def test_an_untracked_symbol_is_a_404_problem(client: AsyncClient) -> None:
    response = await client.get("/prices/DOGEUSDT")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["title"] == "Not Found"
    assert body["instance"] == "/prices/DOGEUSDT"
    assert "BTCUSDT, ETHUSDT, SOLUSDT" in body["detail"]


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("/candles/BTCUSDT?limit=0", "limit"),
        ("/candles/BTCUSDT?limit=501", "limit"),
        ("/stats/BTCUSDT?window=7d", "window"),
    ],
)
async def test_invalid_parameters_are_422_problems(
    client: AsyncClient, path: str, fragment: str
) -> None:
    response = await client.get(path)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert fragment in response.json()["detail"]


async def test_a_damaged_cursor_is_a_400_problem(client: AsyncClient) -> None:
    response = await client.get("/candles/BTCUSDT?cursor=garbage")

    assert response.status_code == 400
    assert "next_cursor" in response.json()["detail"]


async def test_an_unknown_route_is_a_404_problem(client: AsyncClient) -> None:
    response = await client.get("/nope")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_health_reports_the_ingestor(client: AsyncClient) -> None:
    client.app.state.ingestor = SimpleNamespace(  # type: ignore[attr-defined]
        connected=True, lag_seconds=lambda: 0.4, trades_saved=1200
    )

    response = await client.get("/health")

    assert response.json()["ingest"] == {
        "connected": True,
        "lag_seconds": 0.4,
        "trades_saved": 1200,
    }
