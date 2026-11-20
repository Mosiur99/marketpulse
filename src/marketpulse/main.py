import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.agent.llm import LLMClient, OpenAICompatibleClient
from marketpulse.api import ask, docs, health, market, problems
from marketpulse.config import Settings, get_settings
from marketpulse.db import create_engine
from marketpulse.docs.embedder import Embedder
from marketpulse.market.ingestor import Ingestor
from marketpulse.market.store import TradeStore


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = engine or create_engine(settings)
        app.state.ingestor = None
        app.state.llm = llm
        http = None
        if llm is None and settings.llm_api_key is not None:
            http = httpx.AsyncClient(timeout=settings.agent_timeout_seconds)
            app.state.llm = OpenAICompatibleClient(
                http,
                settings.llm_base_url,
                settings.llm_api_key.get_secret_value(),
                settings.llm_model,
            )
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
            if http is not None:
                await http.aclose()
            if owned:
                await app.state.engine.dispose()

    app = FastAPI(
        title="MarketPulse",
        version="0.1.0",
        description=(
            "Live crypto market data recorded from Binance's public trade stream, "
            "and search over the Binance Spot API docs."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.clock = clock
    app.state.embedder = embedder
    problems.install(app)
    app.include_router(health.router)
    app.include_router(market.router)
    app.include_router(docs.router)
    app.include_router(ask.router)
    return app


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(
        create_app(settings), host="0.0.0.0", port=8000, log_level=settings.log_level.lower()
    )
