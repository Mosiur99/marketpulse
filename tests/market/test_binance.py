import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from marketpulse.market.binance import MalformedMessageError, parse_message, stream_url
from tests.market.factories import trade_message


def test_builds_a_combined_stream_url_for_all_symbols() -> None:
    url = stream_url("wss://data-stream.binance.vision/", ["BTCUSDT", "ethusdt"])

    assert url == "wss://data-stream.binance.vision/stream?streams=btcusdt@trade/ethusdt@trade"


def test_parses_a_trade_with_exact_decimals_and_a_utc_time() -> None:
    trade = parse_message(trade_message(42, price="62462.00000001", qty="0.00080000", ms=1234))

    assert trade is not None
    assert trade.symbol == "BTCUSDT"
    assert trade.trade_id == 42
    assert trade.price == Decimal("62462.00000001")
    assert trade.qty == Decimal("0.00080000")
    assert trade.is_buyer_maker is True
    assert trade.traded_at == datetime(2026, 11, 19, 10, 0, 1, 234000, tzinfo=UTC)


def test_parses_bytes_as_well_as_text() -> None:
    assert parse_message(trade_message(1).encode()) is not None


def test_ignores_events_that_are_not_trades() -> None:
    message = json.dumps({"stream": "btcusdt@kline_1m", "data": {"e": "kline", "s": "BTCUSDT"}})

    assert parse_message(message) is None


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"no": "data"}),
        json.dumps(
            {
                "data": {
                    "e": "trade",
                    "s": "BTCUSDT",
                    "t": 1,
                    "p": "abc",
                    "q": "1",
                    "T": 0,
                    "m": True,
                }
            }
        ),
        json.dumps({"data": {"e": "trade", "s": "BTCUSDT", "p": "1", "q": "1", "T": 0, "m": True}}),
    ],
    ids=["not-json", "no-data", "bad-price", "missing-trade-id"],
)
def test_rejects_malformed_messages(raw: str) -> None:
    with pytest.raises(MalformedMessageError):
        parse_message(raw)
