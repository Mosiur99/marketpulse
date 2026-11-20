"""Hybrid search with the real local embedding model (downloads about 70 MB the first time)."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.config import Settings
from marketpulse.docs.chunker import chunk_markdown
from marketpulse.docs.embedder import DIMENSIONS, FastEmbedEmbedder
from marketpulse.docs.fetcher import DocSource
from marketpulse.docs.search import DocSearch
from marketpulse.docs.store import DocStore

pytestmark = [pytest.mark.integration, pytest.mark.model]

SAMPLE = (Path(__file__).parents[1] / "docs" / "fixtures" / "spot-api.md").read_text()
SOURCE = DocSource("https://raw.example/spot-api.md", "https://pages.example/spot-api.md")


@pytest.fixture(scope="module")
def model() -> FastEmbedEmbedder:
    settings = Settings()
    return FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)


def test_vectors_have_the_column_size(model: FastEmbedEmbedder) -> None:
    assert len(model.embed_query("klines")) == DIMENSIONS
    assert [len(v) for v in model.embed_documents(["a", "b"])] == [DIMENSIONS, DIMENSIONS]


@pytest.mark.parametrize(
    ("question", "anchor"),
    [
        ("What is the weight of GET /api/v3/klines?", "klinecandlestick-data"),
        ("how do I get candlestick bars", "klinecandlestick-data"),
        ("what happens if I send too many requests", "limits"),
        ("best bid and ask levels", "order-book"),
    ],
)
async def test_real_model_finds_the_right_section(
    engine: AsyncEngine, model: FastEmbedEmbedder, question: str, anchor: str
) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE doc_sources, doc_chunks"))
    await DocStore(engine).sync(SOURCE, None, chunk_markdown(SAMPLE), model)
    hits = await DocSearch(engine, model).search(question, limit=3)
    assert hits[0].url.endswith(f"#{anchor}")
