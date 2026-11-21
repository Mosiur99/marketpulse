"""The daily summary: written by the agent when an AI provider is set, from stats otherwise."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta

from marketpulse.agent.tools import Market
from marketpulse.alerts.rules import price
from marketpulse.market.candles import minute_of
from marketpulse.market.windows import change_pct

log = logging.getLogger(__name__)

AskAgent = Callable[[str], Awaitable[str | None]]

# One get_stats call per symbol is enough (it has the last price, change, high and low); asking
# for prices too made gpt-oss, which calls one tool per step, run out of steps.
QUESTION = (
    "Write today's short market summary for {symbols}. Call get_stats with window 24h once for "
    "each symbol, then give per symbol the last price, the 24h change in percent, high and low. "
    "Plain text, one line per symbol, no links."
)


async def stats_summary(market: Market, symbols: Sequence[str], now: datetime) -> str:
    lines = []
    since = minute_of(now - timedelta(hours=24))
    for symbol in symbols:
        stats = await market.window_stats(symbol, since)
        if stats is None:
            lines.append(f"{symbol}: no data in the last 24h.")
            continue
        change = change_pct(stats.open, stats.last)
        lines.append(
            f"{symbol}: {price(stats.last)} ({change:+.2f}% over 24h), "
            f"high {price(stats.high)}, low {price(stats.low)}, {stats.trades:,} trades."
        )
    return "\n".join(lines)


async def write_summary(
    market: Market, symbols: Sequence[str], now: datetime, ask_agent: AskAgent | None
) -> str:
    title = f"MarketPulse daily summary, {now:%d %b %Y} ({now:%H:%M} UTC)"
    body = None
    if ask_agent is not None:
        try:
            body = await ask_agent(QUESTION.format(symbols=", ".join(symbols)))
        except Exception:
            log.exception("the agent could not write the summary; using the stats instead")
        else:
            if not body:
                log.warning("the agent gave no summary; using the stats instead")
    if not body:
        body = await stats_summary(market, symbols, now)
    return f"{title}\n\n{body}"
