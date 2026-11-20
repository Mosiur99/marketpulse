from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from marketpulse.market.models import CandleUpdate, Trade


def minute_of(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def aggregate(trades: Iterable[Trade]) -> list[CandleUpdate]:
    """Builds one candle update per symbol and minute. Trade ids, not arrival order, decide
    which trade opens and closes the minute."""
    buckets: dict[tuple[str, datetime], list[Trade]] = defaultdict(list)
    for trade in trades:
        buckets[(trade.symbol, minute_of(trade.traded_at))].append(trade)

    updates = []
    for (symbol, open_time), bucket in sorted(buckets.items()):
        ordered = sorted(bucket, key=lambda trade: trade.trade_id)
        prices = [trade.price for trade in ordered]
        updates.append(
            CandleUpdate(
                symbol=symbol,
                open_time=open_time,
                open=ordered[0].price,
                high=max(prices),
                low=min(prices),
                close=ordered[-1].price,
                volume=sum((trade.qty for trade in ordered), start=Decimal(0)),
                trades=len(ordered),
                first_trade_id=ordered[0].trade_id,
                last_trade_id=ordered[-1].trade_id,
            )
        )
    return updates
