from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.agent.llm import LLMError, RateLimitedError
from marketpulse.agent.tools import ToolBox
from marketpulse.config import Settings
from marketpulse.docs.chunker import chunk_markdown
from marketpulse.docs.fetcher import DocSource
from marketpulse.docs.search import DocSearch
from marketpulse.docs.store import DocStore
from marketpulse.main import create_app
from marketpulse.market.models import Trade
from marketpulse.market.queries import MarketQueries
from marketpulse.market.store import TradeStore
from tests.agent.fakes import ScriptedLLM, answers, asks_for, call
from tests.docs.fakes import HashingEmbedder

pytestmark = pytest.mark.integration

START = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
NOW = START + timedelta(minutes=59, seconds=30)
SAMPLE = (Path(__file__).parents[1] / "docs" / "fixtures" / "spot-api.md").read_text()
SOURCE = DocSource("https://raw.example/spot-api.md", "https://pages.example/spot-api.md")


@pytest.fixture(scope="module")
async def seeded(engine: AsyncEngine) -> HashingEmbedder:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE trades, candles_1m, doc_sources, doc_chunks"))
    await TradeStore(engine).save(
        [
            Trade(
                "BTCUSDT", m + 1, Decimal(100 + m), Decimal(1), False, START + timedelta(minutes=m)
            )
            for m in range(60)
        ]
    )
    embedder = HashingEmbedder()
    await DocStore(engine).sync(SOURCE, None, chunk_markdown(SAMPLE), embedder)
    return embedder


def toolbox(engine: AsyncEngine, embedder: HashingEmbedder) -> ToolBox:
    return ToolBox(MarketQueries(engine), DocSearch(engine, embedder), ["BTCUSDT"], lambda: NOW)


async def test_tools_read_the_database(engine: AsyncEngine, seeded: HashingEmbedder) -> None:
    tools = toolbox(engine, seeded)
    assert '"price": "159' in await tools.run("get_price", '{"symbol": "btcusdt"}')
    stats = await tools.run("get_stats", '{"symbol": "BTCUSDT", "window": "15m"}')
    assert '"open": "144' in stats
    assert '"last": "159' in stats
    candles = await tools.run("get_candles", '{"symbol": "BTCUSDT", "limit": 2}')
    assert candles.count("2026-11-20") == 2
    docs = await tools.run("search_docs", '{"query": "weight of GET /api/v3/klines"}')
    assert "#klinecandlestick-data" in docs
    assert "not instructions" in docs
    assert tools.sources[0].url.endswith("#klinecandlestick-data")
    assert all(c.ok for c in tools.calls)


@pytest.mark.parametrize(
    ("name", "arguments", "error"),
    [
        ("get_price", '{"symbol": "DOGEUSDT"}', "is not tracked"),
        ("get_price", "{}", "symbol: Field required"),
        ("get_price", '{"symbol": "BTCUSDT", "extra": 1}', "extra: Extra inputs"),
        ("get_stats", '{"symbol": "BTCUSDT", "window": "2d"}', "window: Input should be"),
        ("get_candles", '{"symbol": "BTCUSDT", "limit": 500}', "limit: Input should be"),
        ("get_price", "not json", "Invalid arguments"),
        ("place_order", "{}", "Unknown tool place_order"),
    ],
)
async def test_bad_tool_calls_come_back_as_errors_the_model_can_read(
    engine: AsyncEngine, seeded: HashingEmbedder, name: str, arguments: str, error: str
) -> None:
    tools = toolbox(engine, seeded)
    output = await tools.run(name, arguments)
    assert output.startswith('{"error": ')
    assert error in output
    assert tools.calls[-1].ok is False


async def ask(
    engine: AsyncEngine, embedder: HashingEmbedder, llm: ScriptedLLM | None, question: str
) -> tuple[int, dict[str, Any]]:
    app = create_app(
        Settings(ingest_enabled=False, symbols=["BTCUSDT"]),
        engine=engine,
        clock=lambda: NOW,
        embedder=embedder,
        llm=llm,
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post("/ask", json={"question": question})
    return response.status_code, response.json()


async def last_log(engine: AsyncEngine) -> dict[str, object]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT * FROM ask_log ORDER BY id DESC LIMIT 1"))
        ).one()
    return dict(row._mapping)


async def test_ask_answers_with_figures_and_sources_and_logs_the_question(
    engine: AsyncEngine, seeded: HashingEmbedder
) -> None:
    llm = ScriptedLLM(
        [
            asks_for(
                call("get_stats", {"symbol": "BTCUSDT", "window": "1h"}, "s"),
                call("search_docs", {"query": "klines weight"}, "d"),
            ),
            answers("BTC rose 59 (59%) in the hour. GET /api/v3/klines has weight 2."),
        ]
    )
    status, body = await ask(engine, seeded, llm, "How did BTC move and what does klines weigh?")
    assert status == 200
    assert body["status"] == "answered"
    assert "weight 2" in str(body["answer"])
    assert [c["name"] for c in body["tool_calls"]] == ["get_stats", "search_docs"]
    assert any(str(s["url"]).endswith("#klinecandlestick-data") for s in body["sources"])
    assert (body["steps"], body["model"], body["cost"]) == (2, "fake-model", "free tier")
    tool_result = llm.sent[1][3]["content"]
    assert '"change_pct": "59.0000"' in tool_result
    logged = await last_log(engine)
    assert logged["id"] == body["id"]
    assert (logged["status"], logged["steps"], logged["prompt_tokens"]) == ("answered", 2, 200)


async def test_ask_without_a_key_is_unavailable(
    engine: AsyncEngine, seeded: HashingEmbedder
) -> None:
    status, body = await ask(engine, seeded, None, "price of BTC?")
    assert status == 503
    assert "MARKETPULSE_LLM_API_KEY" in str(body["detail"])


@pytest.mark.parametrize(
    ("error", "status"),
    [(RateLimitedError(429, "limit"), 503), (LLMError(401, "bad key"), 502)],
)
async def test_provider_errors_become_problems_and_are_logged(
    engine: AsyncEngine, seeded: HashingEmbedder, error: LLMError, status: int
) -> None:
    code, body = await ask(engine, seeded, ScriptedLLM([error]), "price of BTC?")
    assert code == status
    assert body["status"] == status
    logged = await last_log(engine)
    assert (logged["status"], logged["question"]) == ("error", "price of BTC?")
    assert str(error) in str(logged["error"])


async def test_a_step_limit_still_returns_an_explanation(
    engine: AsyncEngine, seeded: HashingEmbedder
) -> None:
    llm = ScriptedLLM([asks_for(call("get_price", {"symbol": "BTCUSDT"})) for _ in range(6)])
    status, body = await ask(engine, seeded, llm, "keep going")
    assert status == 200
    assert body["status"] == "step_limit"
    assert "allowed number of steps" in str(body["answer"])
