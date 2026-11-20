import json
from datetime import UTC, datetime
from decimal import Decimal

from marketpulse.market.models import Trade

T0 = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


def trade(
    trade_id: int,
    price: str,
    qty: str = "1",
    *,
    second: float = 0,
    minute: int = 0,
    symbol: str = "BTCUSDT",
) -> Trade:
    return Trade(
        symbol=symbol,
        trade_id=trade_id,
        price=Decimal(price),
        qty=Decimal(qty),
        is_buyer_maker=False,
        traded_at=T0.replace(minute=minute, second=int(second)),
    )


def trade_message(
    trade_id: int, price: str = "100.5", qty: str = "0.25", symbol: str = "BTCUSDT", ms: int = 0
) -> str:
    traded_ms = int(T0.timestamp() * 1000) + ms
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@trade",
            "data": {
                "e": "trade",
                "E": traded_ms + 1,
                "s": symbol,
                "t": trade_id,
                "p": price,
                "q": qty,
                "T": traded_ms,
                "m": True,
                "M": True,
            },
        }
    )
