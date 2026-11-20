import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from marketpulse.agent.tools import ToolBox
from marketpulse.docs.search import DocSearch
from marketpulse.evals.snapshot import SnapshotMarket, load_snapshot

SNAPSHOT = load_snapshot(Path(__file__).parents[2] / "evals" / "market-snapshot.json")
MARKET = SnapshotMarket(SNAPSHOT)


def test_snapshot_has_four_hours_of_minutes_for_each_symbol() -> None:
    assert SNAPSHOT.now == datetime(2026, 11, 19, 16, 0, 30, tzinfo=UTC)
    assert set(SNAPSHOT.candles) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    for candles in SNAPSHOT.candles.values():
        assert len(candles) == 240
        assert candles[0].open_time == datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
        assert candles[-1].open_time == datetime(2026, 11, 19, 15, 59, tzinfo=UTC)


async def test_latest_trade_is_the_last_close() -> None:
    trade = await MARKET.latest_trade("BTCUSDT")
    assert trade is not None
    assert trade.price == Decimal("63103.3")
    assert await MARKET.latest_trade("DOGEUSDT") is None


async def test_candles_are_newest_first_and_respect_before() -> None:
    newest = await MARKET.candles("SOLUSDT", 3, None)
    assert [c.close for c in newest] == [Decimal("73.57"), Decimal("73.66"), Decimal("73.79")]
    older = await MARKET.candles("SOLUSDT", 2, newest[-1].open_time)
    assert [c.open_time.minute for c in older] == [56, 55]


async def test_window_stats_cover_candles_from_since() -> None:
    stats = await MARKET.window_stats("ETHUSDT", datetime(2026, 11, 19, 15, 0, tzinfo=UTC))
    assert stats is not None
    assert (stats.open, stats.last, stats.high, stats.low) == (
        Decimal("1798.55"),
        Decimal("1781.89"),
        Decimal("1815.87"),
        Decimal("1768.23"),
    )
    assert stats.trades == 180104
    assert await MARKET.window_stats("ETHUSDT", SNAPSHOT.now) is None


async def test_tools_on_the_snapshot_return_the_figures_the_cases_expect() -> None:
    tools = ToolBox(
        market=MARKET,
        docs=cast(DocSearch, None),
        symbols=list(SNAPSHOT.candles),
        clock=lambda: SNAPSHOT.now,
    )
    stats = json.loads(await tools.run("get_stats", '{"symbol": "ETHUSDT", "window": "1h"}'))
    assert stats["change_pct"] == "-0.9263"
    stats = json.loads(await tools.run("get_stats", '{"symbol": "BTCUSDT", "window": "15m"}'))
    assert (stats["volume"], stats["trades"]) == ("255.29075", 42822)
    stats = json.loads(await tools.run("get_stats", '{"symbol": "SOLUSDT", "window": "4h"}'))
    assert (stats["high"], stats["low"]) == ("75.65", "73.51")
