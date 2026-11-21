from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.alerts.automation import Automation
from marketpulse.alerts.notify import DeliveryError
from marketpulse.alerts.store import MAX_SUMMARY_ATTEMPTS, AlertStore
from marketpulse.alerts.summary import AskAgent
from marketpulse.config import Settings
from marketpulse.evals.snapshot import SnapshotMarket, load_snapshot
from marketpulse.main import create_app

pytestmark = pytest.mark.integration

SNAPSHOT = load_snapshot(Path(__file__).parents[2] / "evals" / "market-snapshot.json")
NOW = SNAPSHOT.now  # 2026-11-19 16:00:30 UTC; ETH fell 0.93% over the last hour


@dataclass
class FakeNotifier:
    fail: bool = False
    sent: list[str] = field(default_factory=list)
    name: str = "fake"

    async def send(self, text: str) -> int:
        if self.fail:
            raise DeliveryError("Telegram 500: down", 3)
        self.sent.append(text)
        return 1


@pytest.fixture
async def store(engine: AsyncEngine) -> AlertStore:
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE alert_rules, notifications RESTART IDENTITY CASCADE")
        )
    return AlertStore(engine)


def automation(
    store: AlertStore,
    notifier: FakeNotifier,
    clock: Callable[[], datetime] = lambda: NOW,
    summary_at: time = time(23, 0),
    ask_agent: AskAgent | None = None,
) -> Automation:
    return Automation(
        store,
        SnapshotMarket(SNAPSHOT),
        notifier,
        ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        summary_at,
        clock,
        ask_agent,
    )


async def statuses(store: AlertStore) -> list[tuple[str, str, int]]:
    return [(n.kind, n.status, n.attempts) for n in reversed(await store.recent(50))]


async def test_a_big_move_alerts_once_even_after_a_restart(store: AlertStore) -> None:
    await store.add_rule("ETHUSDT", "1h", Decimal("0.5"), 60)
    await store.add_rule("BTCUSDT", "1h", Decimal("2"), 60)  # BTC moved 0.72%: no alert
    notifier = FakeNotifier()
    await automation(store, notifier).tick()
    await automation(store, notifier).tick()  # a new process after a restart
    assert notifier.sent == [
        "ETHUSDT fell 0.93% in the last 1h: 1,798.55 to 1,781.89 (15:00-15:59 UTC). "
        "Alert threshold 0.5%."
    ]
    assert await statuses(store) == [("alert", "sent", 1)]


async def test_the_rule_alerts_again_after_its_cooldown(store: AlertStore) -> None:
    await store.add_rule("ETHUSDT", "1h", Decimal("0.5"), 1)
    notifier = FakeNotifier()
    await automation(store, notifier).tick()
    later = NOW + timedelta(minutes=1)
    await automation(store, notifier, clock=lambda: later).tick()
    assert len(notifier.sent) == 2


async def test_stale_data_does_not_alert(store: AlertStore) -> None:
    await store.add_rule("ETHUSDT", "4h", Decimal("0.5"), 60)
    notifier = FakeNotifier()
    hours_later = NOW + timedelta(hours=2)
    await automation(store, notifier, clock=lambda: hours_later).tick()
    assert notifier.sent == []


async def test_a_window_without_enough_data_does_not_alert(store: AlertStore) -> None:
    # The snapshot starts at 12:00, so at 16:00:30 a 24h window holds only 4 hours of data.
    await store.add_rule("SOLUSDT", "24h", Decimal("0.5"), 60)
    await store.add_rule("SOLUSDT", "4h", Decimal("0.5"), 60)
    notifier = FakeNotifier()
    await automation(store, notifier).tick()
    assert len(notifier.sent) == 1
    assert "in the last 4h" in notifier.sent[0]


async def test_the_summary_waits_for_fresh_trades(store: AlertStore) -> None:
    notifier = FakeNotifier()
    hours_later = NOW + timedelta(hours=3)
    await automation(store, notifier, lambda: hours_later, time(8, 0)).tick()
    assert notifier.sent == []
    assert await statuses(store) == []


async def test_a_failed_alert_is_recorded_and_tried_again(store: AlertStore) -> None:
    await store.add_rule("ETHUSDT", "1h", Decimal("0.5"), 60)
    await automation(store, FakeNotifier(fail=True)).tick()
    assert await statuses(store) == [("alert", "failed", 3)]
    assert (await store.recent(1))[0].error == "Telegram 500: down"
    retry = NOW + timedelta(minutes=1)
    working = FakeNotifier()
    await automation(store, working, clock=lambda: retry).tick()
    assert len(working.sent) == 1
    assert await statuses(store) == [("alert", "failed", 3), ("alert", "sent", 1)]


async def test_the_summary_waits_for_its_time_then_goes_once_a_day(store: AlertStore) -> None:
    notifier = FakeNotifier()
    await automation(store, notifier, summary_at=time(16, 1)).tick()
    assert notifier.sent == []
    await automation(store, notifier, summary_at=time(8, 0)).tick()
    await automation(store, notifier, summary_at=time(8, 0)).tick()
    assert len(notifier.sent) == 1
    summary = notifier.sent[0]
    assert summary.startswith("MarketPulse daily summary, 19 Nov 2026 (16:00 UTC)")
    assert "ETHUSDT: 1,781.89" in summary
    assert "high" in summary


async def test_the_summary_uses_the_agent_and_falls_back_to_stats(
    engine: AsyncEngine, store: AlertStore
) -> None:
    questions: list[str] = []

    async def agent(question: str) -> str | None:
        questions.append(question)
        return "BTC 63,103.30, down on the day."

    notifier = FakeNotifier()
    await automation(store, notifier, summary_at=time(8, 0), ask_agent=agent).tick()
    assert notifier.sent[0].endswith("\n\nBTC 63,103.30, down on the day.")
    assert "BTCUSDT, ETHUSDT, SOLUSDT" in questions[0]

    async def broken(question: str) -> str | None:
        raise RuntimeError("provider down")

    async with engine.begin() as connection:  # forget today's summary to write it again
        await connection.execute(text("DELETE FROM notifications"))
    await automation(store, notifier, summary_at=time(8, 0), ask_agent=broken).tick()
    assert "ETHUSDT: 1,781.89 (" in notifier.sent[1]
    assert "over 24h" in notifier.sent[1]


async def test_a_failed_summary_is_retried_a_limited_number_of_times(store: AlertStore) -> None:
    calls = 0

    class Counting(FakeNotifier):
        async def send(self, text: str) -> int:
            nonlocal calls
            calls += 1
            return await super().send(text)

    failing = Counting(fail=True)
    for _ in range(4):
        await automation(store, failing, summary_at=time(8, 0)).tick()
    # Each send makes 3 attempts; after the second the total (6) passes the limit, so it stops.
    assert calls == 2
    assert await statuses(store) == [("summary", "failed", 6)]
    assert MAX_SUMMARY_ATTEMPTS == 5
    assert await store.summary_done("summary:2026-11-19")
    assert await store.claim("summary", "summary:2026-11-19", "again", NOW) is None


async def test_run_keeps_going_after_an_error(store: AlertStore) -> None:
    ticks = 0

    class Flaky(Automation):
        async def tick(self) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 1:
                raise RuntimeError("database restarting")

    async def sleep(seconds: float) -> None:
        if ticks >= 2:
            raise TimeoutError

    flaky = Flaky(store, SnapshotMarket(SNAPSHOT), FakeNotifier(), [], time(8), lambda: NOW)
    with pytest.raises(TimeoutError):
        await flaky.run(60, sleep)
    assert ticks == 2


async def call_api(
    engine: AsyncEngine,
    notifier: FakeNotifier,
    request: Callable[[AsyncClient], Awaitable[Any]],
) -> Any:
    app = create_app(
        Settings(ingest_enabled=False, automation_enabled=False),
        engine=engine,
        notifier=notifier,
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        return await request(client)


async def test_alert_rules_api(engine: AsyncEngine, store: AlertStore) -> None:
    async def scenario(client: AsyncClient) -> None:
        created = await client.post(
            "/alerts/rules", json={"symbol": "eth/usdt", "window": "1h", "threshold_pct": "1.5"}
        )
        assert created.status_code == 201
        rule = created.json()
        assert rule == {
            "id": 1,
            "symbol": "ETHUSDT",
            "window": "1h",
            "threshold_pct": "1.5000",
            "cooldown_minutes": 60,
        }
        assert (await client.get("/alerts/rules")).json() == [rule]
        untracked = await client.post(
            "/alerts/rules", json={"symbol": "DOGEUSDT", "window": "1h", "threshold_pct": 1}
        )
        assert untracked.status_code == 422
        assert "not tracked" in untracked.json()["detail"]
        bad = await client.post(
            "/alerts/rules", json={"symbol": "BTCUSDT", "window": "1h", "threshold_pct": 0}
        )
        assert bad.status_code == 422
        assert (await client.delete("/alerts/rules/1")).status_code == 204
        assert (await client.delete("/alerts/rules/1")).status_code == 404
        assert (await client.get("/alerts/rules")).json() == []

    await call_api(engine, FakeNotifier(), scenario)


async def test_notifications_api_lists_newest_first(engine: AsyncEngine, store: AlertStore) -> None:
    await store.claim("summary", "summary:a", "first", NOW)
    second = await store.claim("summary", "summary:b", "second", NOW)
    assert second is not None
    await store.mark_sent(second, 1, NOW)

    async def scenario(client: AsyncClient) -> Any:
        return (await client.get("/alerts/notifications?limit=5")).json()

    listed = await call_api(engine, FakeNotifier(), scenario)
    assert [(n["text"], n["status"]) for n in listed] == [("second", "sent"), ("first", "pending")]


async def test_test_message_reports_the_channel_or_the_failure(engine: AsyncEngine) -> None:
    async def scenario(client: AsyncClient) -> Any:
        response = await client.post("/alerts/test")
        return response.status_code, response.json()

    working = FakeNotifier()
    assert await call_api(engine, working, scenario) == (200, {"channel": "fake", "attempts": 1})
    assert working.sent == ["MarketPulse test message: alerts are set up."]
    status, body = await call_api(engine, FakeNotifier(fail=True), scenario)
    assert status == 502
    assert "Telegram 500: down" in body["detail"]
