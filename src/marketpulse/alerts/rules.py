"""When a price move is big enough to alert on, and what the message says. Pure functions."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from marketpulse.market.queries import WindowStats
from marketpulse.market.windows import Window, change_pct


@dataclass(frozen=True)
class AlertRule:
    id: int
    symbol: str
    window: Window
    threshold_pct: Decimal
    cooldown_minutes: int


@dataclass(frozen=True)
class Move:
    rule: AlertRule
    change_pct: Decimal
    stats: WindowStats


def check(rule: AlertRule, stats: WindowStats | None) -> Move | None:
    """A move when the price changed by at least the threshold, up or down, over the window."""
    if stats is None or stats.open == 0:
        return None
    change = change_pct(stats.open, stats.last)
    return Move(rule, change, stats) if abs(change) >= rule.threshold_pct else None


def in_cooldown(rule: AlertRule, last_sent: datetime | None, now: datetime) -> bool:
    return last_sent is not None and now - last_sent < timedelta(minutes=rule.cooldown_minutes)


def alert_key(rule: AlertRule, now: datetime) -> str:
    """One key per rule and minute: two checks in the same minute cannot both send."""
    return f"alert:{rule.id}:{now:%Y-%m-%dT%H:%M}"


def summary_key(day: date) -> str:
    return f"summary:{day.isoformat()}"


def summary_due(now: datetime, at: time) -> bool:
    return now.timetz().replace(tzinfo=None) >= at


def price(value: Decimal) -> str:
    places = 2 if abs(value) >= 1 else 6
    return f"{value:,.{places}f}"


def alert_text(move: Move) -> str:
    stats = move.stats
    direction = "rose" if move.change_pct > 0 else "fell"
    return (
        f"{move.rule.symbol} {direction} {abs(move.change_pct):.2f}% in the last "
        f"{move.rule.window}: {price(stats.open)} to {price(stats.last)} "
        f"({stats.first_minute:%H:%M}-{stats.last_minute:%H:%M} UTC). "
        f"Alert threshold {move.rule.threshold_pct.normalize()}%."
    )
