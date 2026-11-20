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
# Keyword search ranks chunks by how many of the question's words they contain, then by ts_rank_cd
# (headings weigh more than body text). Requiring every word lost too much: "what does error -1021
# mean" has no chunk with "mean". Slashes split paths into words, as in the tsv column.
KEYWORD_SQL = text(
    """
    WITH question AS (
        SELECT translate(:query, '/', ' ') AS words
    ),
    terms AS (
        SELECT array_agg(CAST(quote_literal(lexeme) AS tsquery)) AS each_word,
               CAST(replace(CAST(plainto_tsquery('english', q.words) AS text), '&', '|')
                    AS tsquery) AS any_word
        FROM question q, unnest(tsvector_to_array(to_tsvector('english', q.words))) AS lexeme
        GROUP BY q.words
    )
    SELECT c.id
    FROM doc_chunks c, terms t
    WHERE c.tsv @@ t.any_word
    ORDER BY (SELECT count(*) FROM unnest(t.each_word) AS word WHERE c.tsv @@ word) DESC,
             ts_rank_cd(c.tsv, t.any_word) DESC,
             c.id
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


def fused_order(scores: dict[int, float], tie_breaker: Sequence[int]) -> list[int]:
    """Best score first. An exact tie means the lists disagree by the same amount; the last list
    (keyword search in hybrid mode, which matched the literal words) decides it."""
    position = {chunk_id: rank for rank, chunk_id in enumerate(tie_breaker)}
    return sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], position.get(chunk_id, len(position)), chunk_id),
    )


class DocSearch:
    def __init__(self, engine: AsyncEngine, embedder: Embedder) -> None:
        self._engine = engine
        self._embedder = embedder

    async def search(self, query: str, limit: int = 5, mode: Mode = "hybrid") -> list[DocHit]:
        # The keyword list is kept shorter: its lower ranks are mostly chunks that share one common
        # word such as "order", and in the fusion they pushed out good vector-only results.
        vector_candidates, keyword_candidates = max(limit * 4, 20), max(limit * 2, 10)
        rankings: list[list[int]] = []
        async with self._engine.connect() as connection:
            if mode in ("hybrid", "vector"):
                vector = await asyncio.to_thread(self._embedder.embed_query, query)
                rows = await connection.execute(
                    VECTOR_SQL, {"query": str(vector), "limit": vector_candidates}
                )
                rankings.append([row.id for row in rows])
            if mode in ("hybrid", "keyword"):
                rows = await connection.execute(
                    KEYWORD_SQL, {"query": query, "limit": keyword_candidates}
                )
                rankings.append([row.id for row in rows])
            scores = reciprocal_rank_fusion(rankings)
            top = fused_order(scores, rankings[-1])[:limit]
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
