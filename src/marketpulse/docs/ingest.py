"""Fetches each document, chunks it and stores what changed."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from marketpulse.docs.chunker import Chunk, chunk_markdown
from marketpulse.docs.embedder import Embedder
from marketpulse.docs.fetcher import DocSource, fetch
from marketpulse.docs.store import SyncResult

log = logging.getLogger(__name__)


class Store(Protocol):
    async def etag(self, source: DocSource) -> str | None: ...

    async def touch(self, source: DocSource) -> None: ...

    async def sync(
        self, source: DocSource, etag: str | None, chunks: list[Chunk], embedder: Embedder
    ) -> SyncResult: ...


@dataclass(frozen=True)
class SourceReport:
    source: DocSource
    changed: bool
    result: SyncResult | None = None


class DocsIngestor:
    def __init__(
        self,
        store: Store,
        client: httpx.AsyncClient,
        embedder: Embedder,
        delay_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._client = client
        self._embedder = embedder
        self._delay = delay_seconds
        self._sleep = sleep

    async def run(self, sources: Sequence[DocSource], force: bool = False) -> list[SourceReport]:
        """With force, documents are downloaded again even when their ETag has not changed."""
        reports = []
        for index, source in enumerate(sources):
            if index:
                await self._sleep(self._delay)
            etag = None if force else await self._store.etag(source)
            fetched = await fetch(self._client, source.raw_url, etag)
            if fetched is None:
                await self._store.touch(source)
                log.info("%s unchanged", source.raw_url)
                reports.append(SourceReport(source, changed=False))
                continue
            chunks = chunk_markdown(fetched.text)
            result = await self._store.sync(source, fetched.etag, chunks, self._embedder)
            log.info(
                "%s: %d chunks added, %d removed, %d unchanged",
                source.raw_url,
                result.added,
                result.removed,
                result.unchanged,
            )
            reports.append(SourceReport(source, changed=True, result=result))
        return reports
