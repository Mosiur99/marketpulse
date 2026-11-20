import base64
import binascii
from datetime import datetime

from marketpulse.api.problems import ProblemError


def encode_cursor(open_time: datetime) -> str:
    return base64.urlsafe_b64encode(open_time.isoformat().encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> datetime:
    """Turns a cursor from a previous page back into the candle time it points at.
    Cursors are opaque to clients, so a damaged one is a 400, not a 422."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        moment = datetime.fromisoformat(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise ProblemError(400, "The cursor is not valid. Use next_cursor from a page.") from error
    if moment.tzinfo is None:
        raise ProblemError(400, "The cursor is not valid. Use next_cursor from a page.")
    return moment
