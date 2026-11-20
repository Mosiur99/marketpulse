import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta

import pytest

from marketpulse.market.backoff import Backoff
from marketpulse.market.ingestor import Connection, Ingestor
from marketpulse.market.models import Trade
from tests.market.factories import T0, trade_message

Script = list[str] | Exception


class FakeFeed:
    """Plays one script per connection: a list of messages, then the feed closes; or an
    exception raised while connecting. After the last script, connections stay open and idle."""

    def __init__(self, *scripts: Script) -> None:
        self._scripts = list(scripts)
        self.urls: list[str] = []

    def __call__(self, url: str) -> AbstractAsyncContextManager[Connection]:
        self.urls.append(url)
        script = self._scripts.pop(0) if self._scripts else None
        return self._connect(script)

    @asynccontextmanager
    async def _connect(self, script: Script | None) -> AsyncIterator[Connection]:
        if isinstance(script, Exception):
            raise script

        async def messages() -> AsyncIterator[str]:
            for message in script or []:
                yield message
            if script is None:
                await asyncio.Event().wait()

        yield messages()


class FakeSink:
    def __init__(self, failures: int = 0) -> None:
        self.batches: list[list[Trade]] = []
        self._failures = failures

    async def save(self, batch: Sequence[Trade]) -> int:
        if self._failures:
            self._failures -= 1
            raise ConnectionError("database is down")
        self.batches.append(list(batch))
        return len(batch)

    @property
    def trade_ids(self) -> list[int]:
        return [t.trade_id for batch in self.batches for t in batch]


def ingestor(sink: FakeSink, feed: FakeFeed, **options: object) -> Ingestor:
    defaults: dict[str, object] = {
        "batch_size": 100,
        "flush_interval": 0.01,
        "backoff": Backoff(base=0.001, cap=0.001),
        "connector": feed,
    }
    return Ingestor(sink, ["ws://one", "ws://two"], ["BTCUSDT"], **(defaults | options))  # type: ignore[arg-type]


async def run_until(ingest: Ingestor, condition: Callable[[], bool]) -> None:
    task = asyncio.create_task(ingest.run())
    try:
        async with asyncio.timeout(2):
            # The ingestor exposes no events, so the test polls the observable state.
            while not condition():  # noqa: ASYNC110
                await asyncio.sleep(0.005)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_writes_a_batch_as_soon_as_it_is_full() -> None:
    sink = FakeSink()
    feed = FakeFeed([trade_message(i) for i in range(1, 6)])
    ingest = ingestor(sink, feed, batch_size=2, flush_interval=60)

    await run_until(ingest, lambda: len(sink.batches) >= 2)

    assert [len(batch) for batch in sink.batches[:2]] == [2, 2]


async def test_writes_a_partial_batch_on_the_timer() -> None:
    sink = FakeSink()
    ingest = ingestor(sink, FakeFeed([trade_message(1)]), batch_size=100)

    await run_until(ingest, lambda: sink.trade_ids == [1])

    assert ingest.trades_saved == 1


async def test_reconnects_after_the_feed_drops_and_keeps_every_trade() -> None:
    sink = FakeSink()
    feed = FakeFeed([trade_message(1), trade_message(2)], [trade_message(3)])
    ingest = ingestor(sink, feed)

    await run_until(ingest, lambda: sink.trade_ids == [1, 2, 3])

    assert feed.urls[:2] == [feed.urls[0], feed.urls[0]], "a feed that sent trades is reused"


async def test_moves_to_the_next_url_when_a_connection_brings_no_trades() -> None:
    sink = FakeSink()
    feed = FakeFeed(OSError("refused"), [trade_message(1)])
    ingest = ingestor(sink, feed)

    await run_until(ingest, lambda: sink.trade_ids == [1])

    assert [url.split("/stream")[0] for url in feed.urls[:2]] == ["ws://one", "ws://two"]


async def test_skips_malformed_messages_and_counts_them() -> None:
    sink = FakeSink()
    ingest = ingestor(sink, FakeFeed(["{broken", trade_message(1)]))

    await run_until(ingest, lambda: sink.trade_ids == [1])

    assert ingest.malformed_messages == 1


async def test_keeps_trades_when_the_database_fails_and_saves_them_later() -> None:
    sink = FakeSink(failures=2)
    ingest = ingestor(sink, FakeFeed([trade_message(1), trade_message(2)]))

    await run_until(ingest, lambda: sorted(sink.trade_ids) == [1, 2])

    assert ingest.trades_saved == 2


async def test_drops_the_oldest_trades_when_the_buffer_is_full() -> None:
    sink = FakeSink(failures=1)
    ingest = ingestor(sink, FakeFeed(), max_buffer=2)
    for trade_id in (1, 2, 3):
        await ingest._handle(trade_message(trade_id))

    await ingest.flush()
    await ingest.flush()

    assert sink.trade_ids == [2, 3]


async def test_reports_lag_from_the_newest_trade() -> None:
    ingest = ingestor(FakeSink(), FakeFeed(), clock=lambda: T0 + timedelta(seconds=3))
    assert ingest.lag_seconds() is None

    await ingest._handle(trade_message(1))

    assert ingest.lag_seconds() == pytest.approx(3.0)


def test_needs_at_least_one_feed_url() -> None:
    with pytest.raises(ValueError, match="feed URL"):
        Ingestor(FakeSink(), [], ["BTCUSDT"])
