from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.market.models import Trade
from marketpulse.tables import candles_1m, trades


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int


@dataclass(frozen=True, slots=True)
class WindowStats:
    first_minute: datetime
    last_minute: datetime
    open: Decimal
    last: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    trades: int


WINDOW_STATS = text(
    """
    SELECT min(open_time) AS first_minute,
           max(open_time) AS last_minute,
           (array_agg(open ORDER BY open_time))[1] AS open,
           (array_agg(close ORDER BY open_time DESC))[1] AS last,
           max(high) AS high,
           min(low) AS low,
           sum(volume) AS volume,
           sum(trades) AS trades
    FROM candles_1m
    WHERE symbol = :symbol AND open_time >= :since
    """
)


class MarketQueries:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def latest_trade(self, symbol: str) -> Trade | None:
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    select(trades)
                    .where(trades.c.symbol == symbol)
                    .order_by(trades.c.trade_id.desc())
                    .limit(1)
                )
            ).first()
        return Trade(**row._mapping) if row else None

    async def candles(self, symbol: str, limit: int, before: datetime | None) -> list[Candle]:
        """Newest first. `before` is the open time of the last candle on the previous page,
        so pages never skip or repeat a candle even while new ones are being written."""
        query = (
            select(
                candles_1m.c.open_time,
                candles_1m.c.open,
                candles_1m.c.high,
                candles_1m.c.low,
                candles_1m.c.close,
                candles_1m.c.volume,
                candles_1m.c.trades,
            )
            .where(candles_1m.c.symbol == symbol)
            .order_by(candles_1m.c.open_time.desc())
            .limit(limit)
        )
        if before is not None:
            query = query.where(candles_1m.c.open_time < before)
        async with self._engine.connect() as connection:
            rows = await connection.execute(query)
        return [Candle(**row._mapping) for row in rows]

    async def window_stats(self, symbol: str, since: datetime) -> WindowStats | None:
        async with self._engine.connect() as connection:
            row = (await connection.execute(WINDOW_STATS, {"symbol": symbol, "since": since})).one()
        if row.first_minute is None:
            return None
        return WindowStats(**row._mapping)
