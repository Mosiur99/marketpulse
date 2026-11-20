"""Hybrid search: vector similarity and keyword search merged with reciprocal rank fusion."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.docs.embedder import Embedder

Mode = Literal["hybrid", "vector", "keyword"]
RRF_K = 60

VECTOR_SQL = text(
    "SELECT id FROM doc_chunks ORDER BY embedding <=> CAST(:query AS vector) LIMIT :limit"
)
# Keyword search returns the chunks that contain every word of the question. Only when no chunk
# does, it falls back to chunks with any of the words, so a question with one unknown word still
# finds something. Slashes split paths into words, the same way the tsv column is built.
KEYWORD_SQL = text(
    """
    WITH words AS (
        SELECT plainto_tsquery('english', translate(:query, '/', ' ')) AS every_word
    ),
    matches AS (
        SELECT c.id, c.tsv @@ w.every_word AS has_every_word, ts_rank_cd(c.tsv, w.any_word) AS rank
        FROM doc_chunks c,
             (SELECT every_word,
                     CAST(replace(CAST(every_word AS text), '&', '|') AS tsquery) AS any_word
              FROM words) w
        WHERE c.tsv @@ w.any_word
    )
    SELECT id FROM matches
    WHERE has_every_word OR NOT EXISTS (SELECT 1 FROM matches WHERE has_every_word)
    ORDER BY rank DESC, id
    LIMIT :limit
    """
)
DETAILS_SQL = text(
    """
    SELECT c.id, c.heading_path, c.anchor, c.content, s.page_url
    FROM doc_chunks c JOIN doc_sources s ON s.id = c.source_id
    WHERE c.id = ANY(:ids)
    """
)


@dataclass(frozen=True)
class DocHit:
    chunk_id: int
    heading_path: str
    url: str
    content: str
    score: float


def chunk_url(page_url: str, anchor: str) -> str:
    """Text before a document's first heading has no anchor, so it links to the page itself."""
    return f"{page_url}#{anchor}" if anchor else page_url


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> dict[int, float]:
    """Each list adds 1 / (k + rank) for every id it contains; ids found by both lists win."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


class DocSearch:
    def __init__(self, engine: AsyncEngine, embedder: Embedder) -> None:
        self._engine = engine
        self._embedder = embedder

    async def search(self, query: str, limit: int = 5, mode: Mode = "hybrid") -> list[DocHit]:
        candidates = max(limit * 4, 20)
        rankings: list[list[int]] = []
        async with self._engine.connect() as connection:
            if mode in ("hybrid", "vector"):
                vector = await asyncio.to_thread(self._embedder.embed_query, query)
                rows = await connection.execute(
                    VECTOR_SQL, {"query": str(vector), "limit": candidates}
                )
                rankings.append([row.id for row in rows])
            if mode in ("hybrid", "keyword"):
                rows = await connection.execute(KEYWORD_SQL, {"query": query, "limit": candidates})
                rankings.append([row.id for row in rows])
            scores = reciprocal_rank_fusion(rankings)
            top = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
            if not top:
                return []
            details = {row.id: row for row in await connection.execute(DETAILS_SQL, {"ids": top})}
        return [
            DocHit(
                chunk_id=chunk_id,
                heading_path=details[chunk_id].heading_path,
                url=chunk_url(details[chunk_id].page_url, details[chunk_id].anchor),
                content=details[chunk_id].content,
                score=round(scores[chunk_id], 6),
            )
            for chunk_id in top
        ]
