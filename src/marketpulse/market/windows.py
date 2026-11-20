from datetime import timedelta
from decimal import Decimal
from typing import Literal

Window = Literal["15m", "1h", "4h", "24h"]
WINDOWS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}
PERCENT = Decimal("0.0001")


def change_pct(open_: Decimal, last: Decimal) -> Decimal:
    return ((last - open_) / open_ * 100).quantize(PERCENT)
