import random
from collections.abc import Callable


class Backoff:
    """Exponential backoff with equal jitter: half the delay is fixed, half is random, so
    reconnects spread out without ever dropping to zero."""

    def __init__(
        self,
        base: float = 1.0,
        cap: float = 60.0,
        rand: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._base = base
        self._cap = cap
        self._rand = rand
        self.attempt = 0

    def next_delay(self) -> float:
        ceiling = min(self._cap, self._base * 2.0**self.attempt)
        self.attempt += 1
        return ceiling / 2 + self._rand(0, ceiling / 2)

    def reset(self) -> None:
        self.attempt = 0
