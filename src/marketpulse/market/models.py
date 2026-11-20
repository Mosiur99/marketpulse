from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    trade_id: int
    price: Decimal
    qty: Decimal
    is_buyer_maker: bool
    traded_at: datetime


@dataclass(frozen=True, slots=True)
class CandleUpdate:
    """The part of a one-minute candle that a batch of trades contributes."""

    symbol: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int
    first_trade_id: int
    last_trade_id: int
