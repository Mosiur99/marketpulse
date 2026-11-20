from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.config import Settings
from marketpulse.docs.chunker import chunk_markdown
from marketpulse.docs.fetcher import DocSource
from marketpulse.docs.ingest import DocsIngestor
from marketpulse.docs.search import DocSearch, Mode
from marketpulse.docs.store import DocStore
from marketpulse.main import create_app
from tests.docs.fakes import HashingEmbedder

pytestmark = pytest.mark.integration

SAMPLE = (Path(__file__).parents[1] / "docs" / "fixtures" / "spot-api.md").read_text()
SOURCE = DocSource("https://raw.example/spot-api.md", "https://pages.example/spot-api.md")
KLINES_QUESTION = "What is the weight of GET /api/v3/klines?"


async def reset(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE doc_sources, doc_chunks"))


async def chunk_count(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return int(await connection.scalar(text("SELECT count(*) FROM doc_chunks")) or 0)


@pytest.fixture
async def loaded(engine: AsyncEngine) -> HashingEmbedder:
    await reset(engine)
    embedder = HashingEmbedder()
    await DocStore(engine).sync(SOURCE, '"v1"', chunk_markdown(SAMPLE), embedder)
    return embedder


async def test_sync_stores_every_chunk_with_its_etag(engine: AsyncEngine) -> None:
    await reset(engine)
    store = DocStore(engine)
    chunks = chunk_markdown(SAMPLE)
    result = await store.sync(SOURCE, '"v1"', chunks, HashingEmbedder())
    assert (result.added, result.removed, result.unchanged) == (len(chunks), 0, 0)
    assert await chunk_count(engine) == len(chunks)
    assert await store.etag(SOURCE) == '"v1"'


async def test_re_ingesting_the_same_document_adds_and_embeds_nothing(
    engine: AsyncEngine, loaded: HashingEmbedder
) -> None:
    before = await chunk_count(engine)
    embedder = HashingEmbedder()
    result = await DocStore(engine).sync(SOURCE, '"v1"', chunk_markdown(SAMPLE), embedder)
    assert (result.added, result.removed) == (0, 0)
    assert embedder.documents_embedded == 0
    assert await chunk_count(engine) == before


async def test_an_edited_section_replaces_only_its_chunk(
    engine: AsyncEngine, loaded: HashingEmbedder
) -> None:
    before = await chunk_count(engine)
    edited = chunk_markdown(SAMPLE.replace("**Weight:** 25", "**Weight:** 30"))
    result = await DocStore(engine).sync(SOURCE, '"v2"', edited, HashingEmbedder())
    assert (result.added, result.removed) == (1, 1)
    assert await chunk_count(engine) == before
    async with engine.connect() as connection:
        weights = await connection.scalar(
            text("SELECT content FROM doc_chunks WHERE anchor = 'recent-trades-list'")
        )
    assert "**Weight:** 30" in weights


async def test_ingestor_skips_a_document_whose_etag_has_not_changed(engine: AsyncEngine) -> None:
    await reset(engine)
    requests: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text=SAMPLE, headers={"ETag": '"v1"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        ingestor = DocsIngestor(DocStore(engine), client, HashingEmbedder(), delay_seconds=0)
        first = await ingestor.run([SOURCE])
        second = await ingestor.run([SOURCE])
    assert [r.changed for r in first + second] == [True, False]
    assert requests[1].headers["if-none-match"] == '"v1"'
    assert await chunk_count(engine) == len(chunk_markdown(SAMPLE))


@pytest.mark.parametrize("mode", ["hybrid", "keyword"])
async def test_search_finds_the_klines_section_first(
    engine: AsyncEngine, loaded: HashingEmbedder, mode: Mode
) -> None:
    hits = await DocSearch(engine, loaded).search(KLINES_QUESTION, limit=3, mode=mode)
    assert hits[0].url == "https://pages.example/spot-api.md#klinecandlestick-data"
    assert "**Weight:** 2" in hits[0].content
    assert hits[0].heading_path == "Exchange API > Market data > Kline/Candlestick data"


async def test_keyword_search_keeps_only_chunks_with_every_word(
    engine: AsyncEngine, loaded: HashingEmbedder
) -> None:
    # "weight" appears in four endpoint sections, "/api/v3/trades" in one.
    hits = await DocSearch(engine, loaded).search("weight /api/v3/trades", mode="keyword")
    assert [h.url.split("#")[1] for h in hits] == ["recent-trades-list"]


async def test_keyword_search_falls_back_to_any_word(
    engine: AsyncEngine, loaded: HashingEmbedder
) -> None:
    hits = await DocSearch(engine, loaded).search("weight of zebras", mode="keyword")
    anchors = {h.url.split("#")[1] for h in hits}
    assert {"order-book", "recent-trades-list", "klinecandlestick-data"} <= anchors


async def test_vector_search_alone_ranks_by_similarity(
    engine: AsyncEngine, loaded: HashingEmbedder
) -> None:
    # The offline fake embeds bags of words, so "trades list" competes with "klines" here;
    # hybrid search above puts klines first because keyword search agrees.
    hits = await DocSearch(engine, loaded).search(KLINES_QUESTION, limit=3, mode="vector")
    assert "klinecandlestick-data" in [h.url.split("#")[1] for h in hits]
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


async def test_keyword_search_matches_headings_and_ignores_unrelated_text(
    engine: AsyncEngine, loaded: HashingEmbedder
) -> None:
    hits = await DocSearch(engine, loaded).search("HMAC signature", mode="keyword")
    assert [h.url.split("#")[1] for h in hits] == ["signing-requests"]
    assert await DocSearch(engine, loaded).search("zebra", mode="keyword") == []


async def test_search_endpoint(engine: AsyncEngine, loaded: HashingEmbedder) -> None:
    app = create_app(Settings(ingest_enabled=False), engine=engine, embedder=loaded)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        found = await client.get("/docs/search", params={"q": KLINES_QUESTION, "limit": 2})
        too_short = await client.get("/docs/search", params={"q": "k"})
        bad_mode = await client.get("/docs/search", params={"q": "klines", "mode": "magic"})
    assert found.status_code == 200
    body = found.json()
    assert body["mode"] == "hybrid"
    assert len(body["results"]) == 2
    assert body["results"][0]["url"].endswith("#klinecandlestick-data")
    assert too_short.status_code == 422
    assert bad_mode.status_code == 422
    assert too_short.headers["content-type"] == "application/problem+json"
