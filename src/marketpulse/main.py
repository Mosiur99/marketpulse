import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import uvicorn
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.agent.llm import LLMClient, OpenAICompatibleClient
from marketpulse.agent.loop import Agent
from marketpulse.agent.tools import ToolBox
from marketpulse.alerts.automation import Automation
from marketpulse.alerts.notify import LogNotifier, Notifier, TelegramNotifier
from marketpulse.alerts.store import AlertStore
from marketpulse.alerts.summary import AskAgent
from marketpulse.api import alerts, ask, docs, health, market, problems
from marketpulse.api.docs import lazy_embedder
from marketpulse.config import Settings, get_settings
from marketpulse.db import create_engine
from marketpulse.docs.embedder import Embedder
from marketpulse.docs.search import DocSearch
from marketpulse.market.ingestor import Ingestor
from marketpulse.market.queries import MarketQueries
from marketpulse.market.store import TradeStore
from marketpulse.metrics import Metrics


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    notifier: Notifier | None = None,
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
        telegram_http = None
        if notifier is not None:
            app.state.notifier = notifier
        elif settings.telegram_bot_token is not None and settings.telegram_chat_id:
            telegram_http = httpx.AsyncClient(timeout=15)
            app.state.notifier = TelegramNotifier(
                telegram_http,
                settings.telegram_bot_token.get_secret_value(),
                settings.telegram_chat_id,
            )
        else:
            app.state.notifier = LogNotifier()
        automation_task = None
        if settings.automation_enabled:
            automation = Automation(
                AlertStore(app.state.engine),
                MarketQueries(app.state.engine),
                app.state.notifier,
                settings.symbols,
                settings.summary_time_utc,
                clock,
                summary_agent(app) if app.state.llm is not None else None,
                app.state.metrics,
            )
            automation_task = asyncio.create_task(
                automation.run(settings.alert_check_seconds), name="automation"
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
            for task in (automation_task, ingest_task):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            for client in (http, telegram_http):
                if client is not None:
                    await client.aclose()
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
    app.state.ingestor = None
    app.state.metrics = Metrics(lambda: app.state.ingestor)
    app.state.embedder = embedder
    problems.install(app)
    app.include_router(health.router)
    app.include_router(market.router)
    app.include_router(docs.router)
    app.include_router(ask.router)
    app.include_router(alerts.router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(app.state.metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return app


def summary_agent(app: FastAPI) -> AskAgent:
    """The daily summary is written by the same agent and tools as POST /ask."""
    state = app.state

    async def ask_agent(question: str) -> str | None:
        tools = ToolBox(
            market=MarketQueries(state.engine),
            docs=DocSearch(state.engine, lazy_embedder(app)),
            symbols=state.settings.symbols,
            clock=state.clock,
        )
        result = await Agent(
            state.llm,
            tools,
            state.clock,
            # One tool call per symbol and step, plus the answer, with one step to spare.
            max_steps=max(state.settings.agent_max_steps, len(state.settings.symbols) + 2),
            timeout_seconds=state.settings.agent_timeout_seconds,
        ).ask(question)
        state.metrics.llm_tokens.labels("prompt").inc(result.prompt_tokens)
        state.metrics.llm_tokens.labels("completion").inc(result.completion_tokens)
        return result.answer if result.status == "answered" else None

    return ask_agent


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs every request URL at INFO, and Telegram's URL contains the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings), host="0.0.0.0", port=8000, log_level=settings.log_level.lower()
    )
