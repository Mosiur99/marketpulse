from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.config import Settings
from marketpulse.main import create_app
from marketpulse.market.models import Trade
from marketpulse.market.store import TradeStore

pytestmark = pytest.mark.integration

START = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
MINUTES = 150
NOW = START + timedelta(minutes=MINUTES - 1, seconds=30)


def minute_trades(minute: int) -> list[Trade]:
    """Two trades per minute: open at 100 + minute, close one higher."""
    at = START + timedelta(minutes=minute)
    base = Decimal(100 + minute)
    return [
        Trade("BTCUSDT", minute * 2 + 1, base, Decimal("0.5"), False, at),
        Trade(
            "BTCUSDT", minute * 2 + 2, base + 1, Decimal("0.25"), True, at + timedelta(seconds=30)
        ),
    ]


@pytest.fixture(scope="module")
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE trades, candles_1m"))
    await TradeStore(engine).save([t for minute in range(MINUTES) for t in minute_trades(minute)])

    app = create_app(
        Settings(_env_file=None, ingest_enabled=False), engine=engine, clock=lambda: NOW
    )
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def test_returns_the_latest_trade_as_the_price(client: AsyncClient) -> None:
    response = await client.get("/prices/btcusdt")

    assert response.status_code == 200
    assert response.json() == {
        "symbol": "BTCUSDT",
        "price": "250.00000000",
        "traded_at": "2026-11-20T02:29:30Z",
        "trade_id": MINUTES * 2,
    }


async def test_a_tracked_symbol_with_no_trades_is_a_404(client: AsyncClient) -> None:
    response = await client.get("/prices/ETHUSDT")

    assert response.status_code == 404
    assert "No trades" in response.json()["detail"]


async def test_pages_through_every_candle_newest_first_without_gaps(client: AsyncClient) -> None:
    seen: list[str] = []
    page_sizes = []
    url = "/candles/BTCUSDT?limit=60"
    while url:
        body = (await client.get(url)).json()
        page_sizes.append(len(body["candles"]))
        seen += [candle["open_time"] for candle in body["candles"]]
        cursor = body["next_cursor"]
        url = f"/candles/BTCUSDT?limit=60&cursor={cursor}" if cursor else ""

    assert page_sizes == [60, 60, 30]
    assert len(seen) == len(set(seen)) == MINUTES
    assert seen == sorted(seen, reverse=True)


async def test_the_last_page_has_no_cursor_even_when_it_is_exactly_full(
    client: AsyncClient,
) -> None:
    body = (await client.get(f"/candles/BTCUSDT?limit={MINUTES}")).json()

    assert len(body["candles"]) == MINUTES
    assert body["next_cursor"] is None


async def test_a_candle_carries_its_prices_volume_and_trade_count(client: AsyncClient) -> None:
    [candle] = (await client.get("/candles/BTCUSDT?limit=1")).json()["candles"]

    assert candle == {
        "open_time": "2026-11-20T02:29:00Z",
        "open": "249.00000000",
        "high": "250.00000000",
        "low": "249.00000000",
        "close": "250.00000000",
        "volume": "0.75000000",
        "trades": 2,
    }


async def test_stats_cover_the_requested_window(client: AsyncClient) -> None:
    response = await client.get("/stats/BTCUSDT?window=1h")

    body = response.json()
    assert response.status_code == 200
    assert body["first_minute"] == "2026-11-20T01:29:00Z"
    assert body["last_minute"] == "2026-11-20T02:29:00Z"
    assert body["open"] == "189.00000000"
    assert body["last"] == "250.00000000"
    assert body["change"] == "61.00000000"
    assert body["change_pct"] == "32.2751"
    assert body["high"] == "250.00000000"
    assert body["low"] == "189.00000000"
    assert body["trades"] == 122
