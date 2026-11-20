from dataclasses import dataclass, field

import httpx

from marketpulse.docs.chunker import Chunk
from marketpulse.docs.embedder import Embedder
from marketpulse.docs.fetcher import DocSource
from marketpulse.docs.ingest import DocsIngestor
from marketpulse.docs.store import SyncResult
from tests.docs.fakes import HashingEmbedder

A = DocSource("https://raw.example/a.md", "https://pages.example/a.md")
B = DocSource("https://raw.example/b.md", "https://pages.example/b.md")


@dataclass
class FakeStore:
    etags: dict[str, str] = field(default_factory=dict)
    synced: list[tuple[str, str | None, int]] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)

    async def etag(self, source: DocSource) -> str | None:
        return self.etags.get(source.raw_url)

    async def touch(self, source: DocSource) -> None:
        self.touched.append(source.raw_url)

    async def sync(
        self, source: DocSource, etag: str | None, chunks: list[Chunk], embedder: Embedder
    ) -> SyncResult:
        self.synced.append((source.raw_url, etag, len(chunks)))
        return SyncResult(added=len(chunks), removed=0, unchanged=0)


def server(request: httpx.Request) -> httpx.Response:
    if request.headers.get("if-none-match") == '"same"':
        return httpx.Response(304)
    return httpx.Response(
        200, text="## One\n\nFirst.\n\n## Two\n\nSecond.", headers={"ETag": '"new"'}
    )


async def run(store: FakeStore, force: bool = False) -> tuple[list[float], list[bool]]:
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        ingestor = DocsIngestor(store, client, HashingEmbedder(), delay_seconds=1.5, sleep=sleep)
        reports = await ingestor.run([A, B], force=force)
    return pauses, [r.changed for r in reports]


async def test_new_documents_are_chunked_and_stored_with_a_pause_between_fetches() -> None:
    store = FakeStore()
    pauses, changed = await run(store)
    assert pauses == [1.5]
    assert changed == [True, True]
    assert store.synced == [(A.raw_url, '"new"', 2), (B.raw_url, '"new"', 2)]


async def test_unchanged_documents_are_skipped() -> None:
    store = FakeStore(etags={A.raw_url: '"same"'})
    _, changed = await run(store)
    assert changed == [False, True]
    assert store.touched == [A.raw_url]
    assert [url for url, _, _ in store.synced] == [B.raw_url]


async def test_force_downloads_unchanged_documents_again() -> None:
    store = FakeStore(etags={A.raw_url: '"same"', B.raw_url: '"same"'})
    _, changed = await run(store, force=True)
    assert changed == [True, True]
    assert store.touched == []
