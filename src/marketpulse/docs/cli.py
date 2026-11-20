"""marketpulse-docs: downloads, chunks and embeds the Binance Spot API docs."""

import argparse
import asyncio
import logging

import httpx

from marketpulse.config import Settings, get_settings
from marketpulse.db import create_engine
from marketpulse.docs.embedder import FastEmbedEmbedder
from marketpulse.docs.ingest import DocsIngestor, SourceReport
from marketpulse.docs.store import DocStore

USER_AGENT = "marketpulse-docs/0.1 (+https://github.com/Mosiur99/marketpulse)"


async def ingest(settings: Settings, force: bool) -> list[SourceReport]:
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
        ) as client:
            ingestor = DocsIngestor(
                DocStore(engine),
                client,
                FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir),
                delay_seconds=settings.docs_fetch_delay_seconds,
            )
            return await ingestor.run(settings.doc_sources(), force=force)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="download every document even if unchanged"
    )
    args = parser.parse_args()
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    reports = asyncio.run(ingest(settings, args.force))
    for report in reports:
        if report.result is None:
            print(f"{report.source.page_url}: unchanged")
        else:
            r = report.result
            print(f"{report.source.page_url}: +{r.added} -{r.removed} ={r.unchanged}")
