import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from marketpulse.market.models import Trade


class MalformedMessageError(ValueError):
    pass


def stream_url(base_url: str, symbols: list[str]) -> str:
    streams = "/".join(f"{symbol.lower()}@trade" for symbol in symbols)
    return f"{base_url.rstrip('/')}/stream?streams={streams}"


def parse_message(raw: str | bytes) -> Trade | None:
    """Parses one combined-stream message. Returns None for events that are not trades."""
    try:
        message = json.loads(raw)
        data = message["data"]
        if data.get("e") != "trade":
            return None
        return Trade(
            symbol=str(data["s"]),
            trade_id=int(data["t"]),
            price=Decimal(data["p"]),
            qty=Decimal(data["q"]),
            is_buyer_maker=bool(data["m"]),
            traded_at=datetime.fromtimestamp(int(data["T"]) / 1000, tz=UTC),
        )
    except (ValueError, KeyError, TypeError, InvalidOperation) as error:
        raise MalformedMessageError(f"Not a valid trade message: {error!r}") from error
