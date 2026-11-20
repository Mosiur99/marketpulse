import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from marketpulse.market.backoff import Backoff
from marketpulse.market.binance import MalformedMessageError, parse_message, stream_url
from marketpulse.market.models import Trade

log = logging.getLogger(__name__)

Connection = AsyncIterable[str | bytes]
Connector = Callable[[str], AbstractAsyncContextManager[Connection]]


class TradeSink(Protocol):
    async def save(self, batch: Sequence[Trade]) -> int: ...


@asynccontextmanager
async def websocket_connector(url: str) -> AsyncIterator[Connection]:
    async with connect(
        url, open_timeout=10, ping_interval=20, ping_timeout=20, max_size=2**20
    ) as connection:
        yield connection


class Ingestor:
    """Streams trades from the exchange and writes them in batches.

    A batch is written when it reaches `batch_size` trades or every `flush_interval`
    seconds, whichever comes first. When a connection drops, the ingestor reconnects after
    a backoff delay; if that connection delivered no trades, it tries the next feed URL.
    If the database is unavailable, trades stay in memory (up to `max_buffer`, oldest
    dropped first) and are retried on the next flush.
    """

    def __init__(
        self,
        sink: TradeSink,
        base_urls: Sequence[str],
        symbols: Sequence[str],
        *,
        batch_size: int = 500,
        flush_interval: float = 1.0,
        max_buffer: int = 50_000,
        backoff: Backoff | None = None,
        connector: Connector = websocket_connector,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not base_urls:
            raise ValueError("At least one feed URL is required")
        self._sink = sink
        self._urls = [stream_url(base, list(symbols)) for base in base_urls]
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer
        self._backoff = backoff or Backoff()
        self._connector = connector
        self._clock = clock
        self._buffer: list[Trade] = []
        self._flush_lock = asyncio.Lock()
        self.connected = False
        self.last_trade_at: datetime | None = None
        self.trades_saved = 0
        self.malformed_messages = 0

    def lag_seconds(self) -> float | None:
        if self.last_trade_at is None:
            return None
        return max(0.0, (self._clock() - self.last_trade_at).total_seconds())

    async def run(self) -> None:
        flusher = asyncio.create_task(self._flush_periodically())
        try:
            await self._consume_forever()
        finally:
            flusher.cancel()
            await self.flush()

    async def flush(self) -> None:
        async with self._flush_lock:
            if not self._buffer:
                return
            batch = self._buffer
            self._buffer = []
            try:
                self.trades_saved += await self._sink.save(batch)
            except Exception:
                log.exception(
                    "Could not save %d trades; keeping them for the next flush", len(batch)
                )
                self._buffer = (batch + self._buffer)[-self._max_buffer :]

    async def _flush_periodically(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self.flush()

    async def _consume_forever(self) -> None:
        url_index = 0
        while True:
            url = self._urls[url_index]
            received = False
            try:
                async with self._connector(url) as connection:
                    self.connected = True
                    log.info("Connected to %s", url)
                    async for raw in connection:
                        received = await self._handle(raw) or received
                log.info("Feed closed the connection: %s", url)
            except (OSError, TimeoutError, WebSocketException) as error:
                log.warning("Feed connection failed: %s (%r)", url, error)
            finally:
                self.connected = False
            if not received:
                url_index = (url_index + 1) % len(self._urls)
            await asyncio.sleep(self._backoff.next_delay())

    async def _handle(self, raw: str | bytes) -> bool:
        """Buffers the trade in a message. Returns whether the message was a trade."""
        try:
            trade = parse_message(raw)
        except MalformedMessageError as error:
            self.malformed_messages += 1
            log.warning("Skipping a malformed message: %s", error)
            return False
        if trade is None:
            return False
        self._backoff.reset()
        self._buffer.append(trade)
        self.last_trade_at = trade.traded_at
        if len(self._buffer) >= self._batch_size:
            await self.flush()
        return True
