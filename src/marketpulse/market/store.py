from collections.abc import Sequence
from dataclasses import asdict

from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.market.candles import aggregate
from marketpulse.market.models import Trade
from marketpulse.tables import candles_1m, trades


class TradeStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, batch: Sequence[Trade]) -> int:
        """Stores new trades and folds them into their candles in one transaction.
        A trade that is already stored, or appears twice in the batch (the feed can resend
        trades after a reconnect), is saved once and counted once in its candle. Returns how
        many trades were new."""
        unique = list({(trade.symbol, trade.trade_id): trade for trade in reversed(batch)}.values())
        if not unique:
            return 0
        async with self._engine.begin() as connection:
            inserted = await connection.execute(
                insert(trades)
                .values([asdict(trade) for trade in unique])
                .on_conflict_do_nothing()
                .returning(trades.c.symbol, trades.c.trade_id)
            )
            new_keys = {(row.symbol, row.trade_id) for row in inserted}
            new_trades = [t for t in unique if (t.symbol, t.trade_id) in new_keys]
            updates = aggregate(new_trades)
            if updates:
                statement = insert(candles_1m).values([asdict(update) for update in updates])
                incoming = statement.excluded
                await connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[candles_1m.c.symbol, candles_1m.c.open_time],
                        set_={
                            "open": case(
                                (
                                    incoming.first_trade_id < candles_1m.c.first_trade_id,
                                    incoming.open,
                                ),
                                else_=candles_1m.c.open,
                            ),
                            "close": case(
                                (
                                    incoming.last_trade_id > candles_1m.c.last_trade_id,
                                    incoming.close,
                                ),
                                else_=candles_1m.c.close,
                            ),
                            "high": func.greatest(candles_1m.c.high, incoming.high),
                            "low": func.least(candles_1m.c.low, incoming.low),
                            "volume": candles_1m.c.volume + incoming.volume,
                            "trades": candles_1m.c.trades + incoming.trades,
                            "first_trade_id": func.least(
                                candles_1m.c.first_trade_id, incoming.first_trade_id
                            ),
                            "last_trade_id": func.greatest(
                                candles_1m.c.last_trade_id, incoming.last_trade_id
                            ),
                        },
                    )
                )
        return len(new_trades)
