"""A fixed market snapshot, so the answer eval knows every correct figure in advance."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from marketpulse.market.models import Trade
from marketpulse.market.queries import Candle, WindowStats


@dataclass(frozen=True)
class Snapshot:
    now: datetime
    candles: dict[str, list[Candle]]  # oldest first


def load_snapshot(path: Path) -> Snapshot:
    data = json.loads(path.read_text())
    start = datetime.fromisoformat(data["start"])
    return Snapshot(
        now=datetime.fromisoformat(data["now"]),
        candles={
            symbol: [
                Candle(
                    open_time=start + timedelta(minutes=minute),
                    open=Decimal(o),
                    high=Decimal(h),
                    low=Decimal(low),
                    close=Decimal(c),
                    volume=Decimal(v),
                    trades=trades,
                )
                for minute, (o, h, low, c, v, trades) in enumerate(rows)
            ]
            for symbol, rows in data["candles"].items()
        },
    )


class SnapshotMarket:
    """Answers the same three questions as MarketQueries, from the snapshot."""

    def __init__(self, snapshot: Snapshot) -> None:
        self._candles = snapshot.candles

    async def latest_trade(self, symbol: str) -> Trade | None:
        candles = self._candles.get(symbol)
        if not candles:
            return None
        last = candles[-1]
        return Trade(
            symbol=symbol,
            trade_id=0,
            price=last.close,
            qty=Decimal(0),
            is_buyer_maker=False,
            traded_at=last.open_time + timedelta(seconds=59),
        )

    async def candles(self, symbol: str, limit: int, before: datetime | None) -> list[Candle]:
        newest_first = reversed(self._candles.get(symbol, []))
        return [c for c in newest_first if before is None or c.open_time < before][:limit]

    async def window_stats(self, symbol: str, since: datetime) -> WindowStats | None:
        window = [c for c in self._candles.get(symbol, []) if c.open_time >= since]
        if not window:
            return None
        return WindowStats(
            first_minute=window[0].open_time,
            last_minute=window[-1].open_time,
            open=window[0].open,
            last=window[-1].close,
            high=max(c.high for c in window),
            low=min(c.low for c in window),
            volume=sum((c.volume for c in window), Decimal(0)),
            trades=sum(c.trades for c in window),
        )
