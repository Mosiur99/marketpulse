from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from marketpulse.alerts.rules import (
    AlertRule,
    alert_key,
    alert_text,
    check,
    in_cooldown,
    price,
    summary_due,
    summary_key,
)
from marketpulse.market.queries import WindowStats

NOW = datetime(2026, 11, 19, 16, 0, 30, tzinfo=UTC)
RULE = AlertRule(7, "ETHUSDT", "1h", Decimal("0.5"), 60)


def stats(open_: str, last: str) -> WindowStats:
    return WindowStats(
        first_minute=datetime(2026, 11, 19, 15, 0, tzinfo=UTC),
        last_minute=datetime(2026, 11, 19, 15, 59, tzinfo=UTC),
        open=Decimal(open_),
        last=Decimal(last),
        high=Decimal("1815.87"),
        low=Decimal("1768.23"),
        volume=Decimal(1),
        trades=1,
    )


def test_a_move_at_or_above_the_threshold_either_way_alerts() -> None:
    down = check(RULE, stats("1798.55", "1781.89"))
    assert down is not None
    assert down.change_pct == Decimal("-0.9263")
    up = check(RULE, stats("100", "100.5"))
    assert up is not None
    assert up.change_pct == Decimal("0.5000")


def test_a_smaller_move_or_no_data_does_not_alert() -> None:
    assert check(RULE, stats("100", "100.49")) is None
    assert check(RULE, None) is None
    assert check(RULE, stats("0", "1")) is None


def test_cooldown_blocks_until_it_has_passed() -> None:
    assert not in_cooldown(RULE, None, NOW)
    assert in_cooldown(RULE, NOW - timedelta(minutes=59), NOW)
    assert not in_cooldown(RULE, NOW - timedelta(minutes=60), NOW)


def test_keys_identify_the_rule_minute_and_the_summary_day() -> None:
    assert alert_key(RULE, NOW) == "alert:7:2026-11-19T16:00"
    assert alert_key(RULE, NOW + timedelta(seconds=20)) == alert_key(RULE, NOW)
    assert summary_key(date(2026, 11, 19)) == "summary:2026-11-19"


def test_summary_is_due_from_its_time_of_day() -> None:
    assert summary_due(NOW, time(8, 0))
    assert summary_due(NOW, time(16, 0))
    assert not summary_due(NOW, time(16, 1))


def test_alert_text_reads_like_a_sentence() -> None:
    move = check(RULE, stats("1798.55", "1781.89"))
    assert move is not None
    assert alert_text(move) == (
        "ETHUSDT fell 0.93% in the last 1h: 1,798.55 to 1,781.89 (15:00-15:59 UTC). "
        "Alert threshold 0.5%."
    )


def test_prices_below_one_keep_more_decimals() -> None:
    assert price(Decimal("63103.3")) == "63,103.30"
    assert price(Decimal("0.1234567")) == "0.123457"
