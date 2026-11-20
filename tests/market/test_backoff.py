from marketpulse.market.backoff import Backoff


def no_jitter(low: float, high: float) -> float:
    return high


def test_doubles_until_the_cap() -> None:
    backoff = Backoff(base=1, cap=8, rand=no_jitter)

    assert [backoff.next_delay() for _ in range(6)] == [1, 2, 4, 8, 8, 8]


def test_jitter_keeps_at_least_half_the_delay() -> None:
    backoff = Backoff(base=1, cap=60, rand=lambda low, high: low)

    assert [backoff.next_delay() for _ in range(4)] == [0.5, 1, 2, 4]


def test_reset_starts_again_from_the_base_delay() -> None:
    backoff = Backoff(base=1, cap=60, rand=no_jitter)
    backoff.next_delay()
    backoff.next_delay()

    backoff.reset()

    assert backoff.next_delay() == 1
