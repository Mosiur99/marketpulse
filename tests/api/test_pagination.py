from datetime import UTC, datetime

import pytest

from marketpulse.api.pagination import decode_cursor, encode_cursor
from marketpulse.api.problems import ProblemError


def test_a_cursor_round_trips_to_the_same_moment() -> None:
    moment = datetime(2026, 11, 20, 4, 17, tzinfo=UTC)

    assert decode_cursor(encode_cursor(moment)) == moment


def test_a_cursor_is_url_safe() -> None:
    cursor = encode_cursor(datetime(2026, 11, 20, 4, 17, tzinfo=UTC))

    assert cursor.replace("-", "").replace("_", "").isalnum()


@pytest.mark.parametrize(
    "cursor",
    ["not-a-cursor", "%%%", encode_cursor(datetime(2026, 1, 1)).rstrip("=")],
    ids=["garbage", "not-base64", "no-timezone"],
)
def test_rejects_a_damaged_cursor_with_a_400(cursor: str) -> None:
    with pytest.raises(ProblemError) as raised:
        decode_cursor(cursor)

    assert raised.value.status == 400
