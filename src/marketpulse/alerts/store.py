"""Alert rules and the notifications outbox."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.alerts.rules import AlertRule
from marketpulse.market.windows import Window
from marketpulse.tables import alert_rules, notifications

Kind = Literal["alert", "summary"]
# A failed daily summary is tried again on later checks, up to this many times in total.
MAX_SUMMARY_ATTEMPTS = 5


@dataclass(frozen=True)
class Notification:
    id: int
    kind: str
    rule_id: int | None
    text: str
    status: str
    attempts: int
    error: str | None
    created_at: datetime
    sent_at: datetime | None


def to_rule(row: Row[Any]) -> AlertRule:
    return AlertRule(row.id, row.symbol, row.window, row.threshold_pct, row.cooldown_minutes)


class AlertStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def add_rule(
        self, symbol: str, window: Window, threshold_pct: Decimal, cooldown_minutes: int
    ) -> AlertRule:
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    insert(alert_rules)
                    .values(
                        symbol=symbol,
                        window=window,
                        threshold_pct=threshold_pct,
                        cooldown_minutes=cooldown_minutes,
                    )
                    .returning(alert_rules)
                )
            ).one()
        return to_rule(row)

    async def rules(self) -> list[AlertRule]:
        async with self._engine.connect() as connection:
            rows = await connection.execute(select(alert_rules).order_by(alert_rules.c.id))
        return [to_rule(row) for row in rows]

    async def delete_rule(self, rule_id: int) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(alert_rules).where(alert_rules.c.id == rule_id)
            )
        return result.rowcount > 0

    async def last_alert(self, rule_id: int) -> datetime | None:
        """When the rule last alerted. Failed sends do not count, so they do not block a retry."""
        async with self._engine.connect() as connection:
            last: datetime | None = await connection.scalar(
                select(func.max(notifications.c.created_at)).where(
                    notifications.c.rule_id == rule_id,
                    notifications.c.status.in_(("pending", "sent")),
                )
            )
        return last

    async def summary_done(self, key: str) -> bool:
        """True when this day's summary was sent, is being sent, or failed too often to retry."""
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    select(notifications.c.status, notifications.c.attempts).where(
                        notifications.c.dedupe_key == key
                    )
                )
            ).first()
        if row is None:
            return False
        return bool(row.status != "failed" or row.attempts >= MAX_SUMMARY_ATTEMPTS)

    async def claim(
        self, kind: Kind, key: str, text: str, now: datetime, rule_id: int | None = None
    ) -> int | None:
        """Records the message before sending it. Returns None when the key was already used,
        unless it is a summary that failed fewer than MAX_SUMMARY_ATTEMPTS times."""
        new = pg_insert(notifications).values(
            kind=kind, dedupe_key=key, rule_id=rule_id, text=text, status="pending", created_at=now
        )
        statement = new.on_conflict_do_update(
            index_elements=[notifications.c.dedupe_key],
            set_={"status": "pending", "text": new.excluded.text, "error": None},
            where=(notifications.c.status == "failed")
            & (notifications.c.kind == "summary")
            & (notifications.c.attempts < MAX_SUMMARY_ATTEMPTS),
        ).returning(notifications.c.id)
        async with self._engine.begin() as connection:
            claimed: int | None = await connection.scalar(statement)
        return claimed

    async def mark_sent(self, notification_id: int, attempts: int, now: datetime) -> None:
        await self._finish(notification_id, attempts, status="sent", sent_at=now)

    async def mark_failed(self, notification_id: int, attempts: int, error: str) -> None:
        await self._finish(notification_id, attempts, status="failed", error=error)

    async def _finish(self, notification_id: int, attempts: int, **values: object) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                update(notifications)
                .where(notifications.c.id == notification_id)
                .values(attempts=notifications.c.attempts + attempts, **values)
            )

    async def recent(self, limit: int) -> list[Notification]:
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                select(notifications).order_by(notifications.c.id.desc()).limit(limit)
            )
        return [
            Notification(
                r.id,
                r.kind,
                r.rule_id,
                r.text,
                r.status,
                r.attempts,
                r.error,
                r.created_at,
                r.sent_at,
            )
            for r in rows
        ]
