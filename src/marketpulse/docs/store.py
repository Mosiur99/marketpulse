"""Keeps doc_chunks in step with the latest version of each document."""

import asyncio
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.docs.chunker import Chunk
from marketpulse.docs.embedder import Embedder
from marketpulse.docs.fetcher import DocSource
from marketpulse.tables import doc_chunks, doc_sources


@dataclass(frozen=True)
class SyncResult:
    added: int
    removed: int
    unchanged: int


class DocStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def etag(self, source: DocSource) -> str | None:
        async with self._engine.connect() as connection:
            return await connection.scalar(
                select(doc_sources.c.etag).where(doc_sources.c.url == source.raw_url)
            )

    async def touch(self, source: DocSource) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                update(doc_sources)
                .where(doc_sources.c.url == source.raw_url)
                .values(fetched_at=func.now())
            )

    async def sync(
        self, source: DocSource, etag: str | None, chunks: list[Chunk], embedder: Embedder
    ) -> SyncResult:
        """Adds chunks that are new, deletes chunks that are gone and leaves the rest alone.

        Only new chunks are embedded, so re-ingesting an unchanged document costs nothing.
        """
        wanted = {chunk.content_hash: chunk for chunk in chunks}
        async with self._engine.connect() as connection:
            stored = set(
                await connection.scalars(
                    select(doc_chunks.c.content_hash)
                    .join(doc_sources)
                    .where(doc_sources.c.url == source.raw_url)
                )
            )
        new = [chunk for key, chunk in wanted.items() if key not in stored]
        vectors = await asyncio.to_thread(
            embedder.embed_documents, [chunk.embedding_text for chunk in new]
        )

        async with self._engine.begin() as connection:
            upsert = pg_insert(doc_sources).values(
                url=source.raw_url, page_url=source.page_url, etag=etag, fetched_at=func.now()
            )
            source_id = await connection.scalar(
                upsert.on_conflict_do_update(
                    index_elements=[doc_sources.c.url],
                    set_={"page_url": source.page_url, "etag": etag, "fetched_at": func.now()},
                ).returning(doc_sources.c.id)
            )
            gone = stored - wanted.keys()
            if gone:
                await connection.execute(
                    delete(doc_chunks).where(
                        doc_chunks.c.source_id == source_id,
                        doc_chunks.c.content_hash.in_(gone),
                    )
                )
            if new:
                await connection.execute(
                    insert(doc_chunks),
                    [
                        {
                            "source_id": source_id,
                            "heading_path": chunk.heading_path,
                            "anchor": chunk.anchor,
                            "content": chunk.content,
                            "content_hash": chunk.content_hash,
                            "embedding": vector,
                        }
                        for chunk, vector in zip(new, vectors, strict=True)
                    ],
                )
        return SyncResult(added=len(new), removed=len(gone), unchanged=len(wanted) - len(new))
