import asyncio
import contextlib

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from websockets.asyncio.server import ServerConnection, serve

from marketpulse.market.backoff import Backoff
from marketpulse.market.ingestor import Ingestor
from marketpulse.market.store import TradeStore
from marketpulse.tables import candles_1m, trades
from tests.market.factories import trade_message

pytestmark = pytest.mark.integration


async def test_ingests_from_a_real_websocket_and_survives_a_dropped_connection(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE trades, candles_1m"))

    connections = 0

    async def feed(websocket: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        if connections == 1:
            for trade_id in (1, 2, 3):
                await websocket.send(trade_message(trade_id, ms=trade_id))
            await websocket.close()  # like Binance closing a connection
        else:
            for trade_id in (3, 4):  # trade 3 is sent again after the reconnect
                await websocket.send(trade_message(trade_id, ms=trade_id))
            await websocket.wait_closed()

    async with serve(feed, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ingestor = Ingestor(
            TradeStore(engine),
            [f"ws://127.0.0.1:{port}"],
            ["BTCUSDT"],
            flush_interval=0.05,
            backoff=Backoff(base=0.01, cap=0.01),
        )
        task = asyncio.create_task(ingestor.run())
        try:
            async with asyncio.timeout(5):
                while ingestor.trades_saved < 4:  # noqa: ASYNC110
                    await asyncio.sleep(0.02)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async with engine.connect() as connection:
        trade_count = await connection.scalar(select(func.count()).select_from(trades))
        candle_trades = await connection.scalar(select(candles_1m.c.trades))

    assert connections == 2
    assert trade_count == 4
    assert candle_trades == 4
