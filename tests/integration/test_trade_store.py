from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.market.store import TradeStore
from marketpulse.tables import candles_1m
from tests.market.factories import trade

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def empty_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE trades, candles_1m"))


async def candles(engine: AsyncEngine) -> list[dict[str, object]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(candles_1m).order_by(candles_1m.c.symbol, candles_1m.c.open_time)
        )
        return [dict(row._mapping) for row in rows]


async def test_saves_trades_and_builds_their_candle(engine: AsyncEngine) -> None:
    store = TradeStore(engine)

    saved = await store.save([trade(1, "100", "0.5"), trade(2, "105", "0.25"), trade(3, "99")])

    assert saved == 3
    [candle] = await candles(engine)
    assert (candle["open"], candle["high"], candle["low"], candle["close"]) == (
        Decimal("100"),
        Decimal("105"),
        Decimal("99"),
        Decimal("99"),
    )
    assert candle["volume"] == Decimal("1.75")
    assert candle["trades"] == 3


async def test_a_resent_trade_is_stored_once_and_counted_once(engine: AsyncEngine) -> None:
    store = TradeStore(engine)
    await store.save([trade(1, "100"), trade(2, "101")])

    saved = await store.save([trade(2, "101"), trade(3, "102")])

    assert saved == 1
    [candle] = await candles(engine)
    assert candle["trades"] == 3
    assert candle["volume"] == Decimal("3")
    assert candle["close"] == Decimal("102")


async def test_a_trade_twice_in_one_batch_is_counted_once(engine: AsyncEngine) -> None:
    saved = await TradeStore(engine).save([trade(1, "100"), trade(2, "101"), trade(2, "101")])

    assert saved == 2
    [candle] = await candles(engine)
    assert candle["trades"] == 2
    assert candle["volume"] == Decimal("2")


async def test_merging_batches_keeps_open_and_close_in_trade_order(engine: AsyncEngine) -> None:
    store = TradeStore(engine)
    await store.save([trade(5, "100"), trade(6, "104")])

    await store.save([trade(4, "98"), trade(3, "97"), trade(7, "103")])

    [candle] = await candles(engine)
    assert candle["open"] == Decimal("97")
    assert candle["close"] == Decimal("103")
    assert candle["high"] == Decimal("104")
    assert candle["low"] == Decimal("97")
    assert (candle["first_trade_id"], candle["last_trade_id"]) == (3, 7)


async def test_late_trades_never_move_the_close_backwards(engine: AsyncEngine) -> None:
    store = TradeStore(engine)
    await store.save([trade(10, "100")])

    await store.save([trade(9, "50")])

    [candle] = await candles(engine)
    assert candle["close"] == Decimal("100")
    assert candle["open"] == Decimal("50")


async def test_saving_nothing_is_a_no_op(engine: AsyncEngine) -> None:
    assert await TradeStore(engine).save([]) == 0
