"""Calls the real AI provider. Run on demand with `uv run pytest -m live`; needs the API key."""

from datetime import UTC, datetime

import httpx
import pytest

from marketpulse.agent.llm import OpenAICompatibleClient
from marketpulse.agent.loop import Agent
from marketpulse.agent.tools import CallRecord, Source
from marketpulse.config import Settings

pytestmark = pytest.mark.live


class PriceTools:
    """Returns a fixed realistic price; an echo of the arguments makes the model loop."""

    def __init__(self) -> None:
        self.symbols = ["BTCUSDT"]
        self.sources: list[Source] = []
        self.calls: list[CallRecord] = []

    async def run(self, name: str, arguments: str) -> str:
        self.calls.append(CallRecord(name, arguments, True))
        return '{"symbol": "BTCUSDT", "price": "63103.3", "traded_at": "2026-11-19 15:59:59+00:00"}'


async def test_the_model_calls_a_tool_and_then_answers() -> None:
    settings = Settings()
    if settings.llm_api_key is None:
        pytest.skip("MARKETPULSE_LLM_API_KEY is not set")
    async with httpx.AsyncClient(timeout=60) as http:
        llm = OpenAICompatibleClient(
            http, settings.llm_base_url, settings.llm_api_key.get_secret_value(), settings.llm_model
        )
        tools = PriceTools()
        result = await Agent(llm, tools, lambda: datetime.now(UTC)).ask(
            "What is the latest BTCUSDT price?"
        )
    assert result.status == "answered", result
    assert [c.name for c in tools.calls][:1] == ["get_price"]
    assert result.answer
    assert "63,103.3" in result.answer or "63103.3" in result.answer
    assert result.prompt_tokens > 0
