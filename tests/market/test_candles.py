from decimal import Decimal

from marketpulse.market.candles import aggregate
from tests.market.factories import T0, trade


def test_builds_one_candle_per_symbol_and_minute() -> None:
    updates = aggregate(
        [
            trade(1, "100", second=5),
            trade(2, "101", second=59),
            trade(3, "102", minute=1),
            trade(4, "50", symbol="ETHUSDT"),
        ]
    )

    assert [(u.symbol, u.open_time.minute, u.trades) for u in updates] == [
        ("BTCUSDT", 0, 2),
        ("BTCUSDT", 1, 1),
        ("ETHUSDT", 0, 1),
    ]
    assert updates[0].open_time == T0


def test_open_and_close_follow_trade_ids_not_arrival_order() -> None:
    [candle] = aggregate([trade(7, "105"), trade(5, "100"), trade(9, "103"), trade(6, "99")])

    assert candle.open == Decimal("100")
    assert candle.close == Decimal("103")
    assert candle.high == Decimal("105")
    assert candle.low == Decimal("99")
    assert (candle.first_trade_id, candle.last_trade_id) == (5, 9)


def test_sums_volume_exactly() -> None:
    [candle] = aggregate([trade(1, "10", qty="0.1"), trade(2, "10", qty="0.2")])

    assert candle.volume == Decimal("0.3")


def test_returns_nothing_for_no_trades() -> None:
    assert aggregate([]) == []
