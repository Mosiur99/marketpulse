"""Checks the alert rules every minute and sends the daily summary once a day."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, time, timedelta

from marketpulse.agent.tools import Market
from marketpulse.alerts.notify import DeliveryError, Notifier
from marketpulse.alerts.rules import (
    alert_key,
    alert_text,
    check,
    in_cooldown,
    summary_due,
    summary_key,
)
from marketpulse.alerts.store import AlertStore, Kind
from marketpulse.alerts.summary import AskAgent, write_summary
from marketpulse.market.candles import minute_of
from marketpulse.market.windows import WINDOWS

log = logging.getLogger(__name__)

# Data older than this means the ingestor is behind; alerting on it would report an old move.
FRESH = timedelta(minutes=5)
# A window counts as covered when its first candle is at most this far after the window start.
# Just after a start-up, "the last 15m" may hold one minute of data; that is not a 15m move.
COVERAGE_SLACK = timedelta(minutes=2)


class Automation:
    def __init__(
        self,
        store: AlertStore,
        market: Market,
        notifier: Notifier,
        symbols: Sequence[str],
        summary_at: time,
        clock: Callable[[], datetime],
        ask_agent: AskAgent | None = None,
    ) -> None:
        self._store = store
        self._market = market
        self._notifier = notifier
        self._symbols = list(symbols)
        self._summary_at = summary_at
        self._clock = clock
        self._ask_agent = ask_agent

    async def run(
        self, interval: float, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("alert check failed; trying again next time")
            await sleep(interval)

    async def tick(self) -> None:
        await self.check_alerts()
        await self.send_summary_if_due()

    async def check_alerts(self) -> None:
        now = self._clock()
        for rule in await self._store.rules():
            if in_cooldown(rule, await self._store.last_alert(rule.id), now):
                continue
            since = minute_of(now - WINDOWS[rule.window])
            stats = await self._market.window_stats(rule.symbol, since)
            if (
                stats is None
                or now - stats.last_minute > FRESH
                or stats.first_minute - since > COVERAGE_SLACK
            ):
                continue
            move = check(rule, stats)
            if move is not None:
                await self._deliver("alert", alert_key(rule, now), alert_text(move), now, rule.id)

    async def send_summary_if_due(self) -> None:
        now = self._clock()
        key = summary_key(now.date())
        if not summary_due(now, self._summary_at) or await self._store.summary_done(key):
            return
        if not await self._all_fresh(now):
            # Straight after a start-up the newest data can be hours old; wait for live trades.
            log.info("daily summary waits for fresh trades for every symbol")
            return
        text = await write_summary(self._market, self._symbols, now, self._ask_agent)
        await self._deliver("summary", key, text, now)

    async def _all_fresh(self, now: datetime) -> bool:
        for symbol in self._symbols:
            trade = await self._market.latest_trade(symbol)
            if trade is None or now - trade.traded_at > FRESH:
                return False
        return True

    async def _deliver(
        self, kind: Kind, key: str, text: str, now: datetime, rule_id: int | None = None
    ) -> None:
        notification_id = await self._store.claim(kind, key, text, now, rule_id)
        if notification_id is None:
            return
        try:
            attempts = await self._notifier.send(text)
        except DeliveryError as error:
            log.warning("%s %s not delivered: %s", kind, key, error)
            await self._store.mark_failed(notification_id, error.attempts, str(error))
            return
        await self._store.mark_sent(notification_id, attempts, self._clock())
