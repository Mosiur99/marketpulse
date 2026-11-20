from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel

from marketpulse.api.pagination import decode_cursor, encode_cursor
from marketpulse.api.problems import ProblemError
from marketpulse.market.candles import minute_of
from marketpulse.market.queries import MarketQueries
from marketpulse.market.windows import WINDOWS, Window, change_pct

router = APIRouter(tags=["market data"])


class Price(BaseModel):
    symbol: str
    price: Decimal
    traded_at: datetime
    trade_id: int


class CandleOut(BaseModel):
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int


class CandlePage(BaseModel):
    symbol: str
    interval: Literal["1m"] = "1m"
    candles: list[CandleOut]
    next_cursor: str | None


class Stats(BaseModel):
    symbol: str
    window: Window
    first_minute: datetime
    last_minute: datetime
    open: Decimal
    last: Decimal
    change: Decimal
    change_pct: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    trades: int


def queries(request: Request) -> MarketQueries:
    return MarketQueries(request.app.state.engine)


def known_symbol(
    request: Request,
    symbol: Annotated[str, Path(description="Trading pair, for example BTCUSDT")],
) -> str:
    normalized = symbol.upper()
    if normalized not in request.app.state.settings.symbols:
        tracked = ", ".join(request.app.state.settings.symbols)
        raise ProblemError(404, f"{symbol} is not tracked. Tracked symbols: {tracked}.")
    return normalized


Symbol = Annotated[str, Depends(known_symbol)]
Queries = Annotated[MarketQueries, Depends(queries)]


@router.get("/prices/{symbol}", response_model=Price)
async def latest_price(symbol: Symbol, market: Queries) -> Price:
    trade = await market.latest_trade(symbol)
    if trade is None:
        raise ProblemError(404, f"No trades recorded for {symbol} yet.")
    return Price(
        symbol=symbol, price=trade.price, traded_at=trade.traded_at, trade_id=trade.trade_id
    )


@router.get("/candles/{symbol}", response_model=CandlePage)
async def candles(
    symbol: Symbol,
    market: Queries,
    limit: Annotated[int, Query(ge=1, le=500, description="Candles per page")] = 60,
    cursor: Annotated[str | None, Query(description="next_cursor from the previous page")] = None,
) -> CandlePage:
    before = decode_cursor(cursor) if cursor else None
    rows = await market.candles(symbol, limit + 1, before)
    page, more = rows[:limit], len(rows) > limit
    return CandlePage(
        symbol=symbol,
        candles=[CandleOut.model_validate(row, from_attributes=True) for row in page],
        next_cursor=encode_cursor(page[-1].open_time) if more else None,
    )


@router.get("/stats/{symbol}", response_model=Stats)
async def stats(
    request: Request,
    symbol: Symbol,
    market: Queries,
    window: Annotated[Window, Query(description="How far back to look")] = "24h",
) -> Stats:
    since = minute_of(request.app.state.clock() - WINDOWS[window])
    found = await market.window_stats(symbol, since)
    if found is None:
        raise ProblemError(404, f"No candles for {symbol} in the last {window}.")
    change = found.last - found.open
    return Stats(
        symbol=symbol,
        window=window,
        first_minute=found.first_minute,
        last_minute=found.last_minute,
        open=found.open,
        last=found.last,
        change=change,
        change_pct=change_pct(found.open, found.last),
        high=found.high,
        low=found.low,
        volume=found.volume,
        trades=found.trades,
    )
