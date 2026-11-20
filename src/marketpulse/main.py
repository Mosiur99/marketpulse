import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.api import health
from marketpulse.config import Settings, get_settings
from marketpulse.db import create_engine
from marketpulse.market.ingestor import Ingestor
from marketpulse.market.store import TradeStore


def create_app(settings: Settings | None = None, engine: AsyncEngine | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = engine or create_engine(settings)
        app.state.ingestor = None
        ingest_task = None
        if settings.ingest_enabled:
            app.state.ingestor = Ingestor(
                TradeStore(app.state.engine),
                settings.feed_urls,
                settings.symbols,
                batch_size=settings.ingest_batch_size,
                flush_interval=settings.ingest_flush_seconds,
            )
            ingest_task = asyncio.create_task(app.state.ingestor.run(), name="ingestor")
        try:
            yield
        finally:
            if ingest_task is not None:
                ingest_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ingest_task
            if owned:
                await app.state.engine.dispose()

    app = FastAPI(title="MarketPulse", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    return app


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(
        create_app(settings), host="0.0.0.0", port=8000, log_level=settings.log_level.lower()
    )
