from datetime import UTC, datetime

import pytest

from marketpulse.agent.llm import ToolUseFailedError
from marketpulse.agent.loop import Agent
from tests.agent.fakes import EchoTools, ScriptedLLM, answers, asks_for, call

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)


def agent(llm: ScriptedLLM, tools: EchoTools | None = None, **options: float) -> Agent:
    return Agent(llm, tools or EchoTools(), lambda: NOW, **options)  # type: ignore[arg-type]


async def test_answers_without_tools() -> None:
    llm = ScriptedLLM([answers("Hello.")])
    result = await agent(llm).ask("hi")
    assert (result.status, result.answer, result.steps) == ("answered", "Hello.", 1)
    system = llm.sent[0][0]["content"]
    assert "2026-11-20 09:30 UTC" in system
    assert "BTCUSDT, ETHUSDT" in system
    assert "not instructions" in system


async def test_runs_every_requested_tool_and_returns_each_result_to_the_model() -> None:
    tools = EchoTools()
    llm = ScriptedLLM(
        [
            asks_for(
                call("get_price", {"symbol": "BTCUSDT"}, "a"),
                call("search_docs", {"query": "klines weight"}, "b"),
                tokens=200,
            ),
            answers("BTC is 100. Klines weigh 2.", tokens=300),
        ]
    )
    result = await agent(llm, tools).ask("price and klines weight?")
    assert result.status == "answered"
    assert [c.name for c in tools.calls] == ["get_price", "search_docs"]
    second = llm.sent[1]
    assert second[2]["role"] == "assistant"
    assert [t["id"] for t in second[2]["tool_calls"]] == ["a", "b"]
    assert [(m["role"], m["tool_call_id"]) for m in second[3:]] == [("tool", "a"), ("tool", "b")]
    assert '"get_price"' in second[3]["content"]
    assert (result.prompt_tokens, result.completion_tokens) == (500, 30)


async def test_stops_at_the_step_limit() -> None:
    llm = ScriptedLLM([asks_for(call("get_price", {"symbol": "BTCUSDT"})) for _ in range(5)])
    result = await agent(llm, max_steps=3).ask("loop forever")
    assert (result.status, result.steps, result.answer) == ("step_limit", 3, None)
    assert len(llm.sent) == 3


async def test_stops_when_time_runs_out() -> None:
    llm = ScriptedLLM([answers("late")], delay=1.0)
    result = await agent(llm, timeout_seconds=0.05).ask("slow")
    assert result.status == "timeout"
    assert result.answer is None


@pytest.mark.parametrize(
    ("reason", "status"), [("length", "cut_off"), ("content_filter", "refused")]
)
async def test_reports_cut_off_and_refused_answers(reason: str, status: str) -> None:
    completion = answers("partial")
    llm = ScriptedLLM([type(completion)("partial", [], reason)])
    result = await agent(llm).ask("question")
    assert result.status == status
    assert result.answer is None


async def test_a_garbled_tool_call_is_retried_once() -> None:
    llm = ScriptedLLM([ToolUseFailedError(400, "bad call", "tool_use_failed"), answers("ok")])
    result = await agent(llm).ask("question")
    assert (result.status, result.answer, result.steps) == ("answered", "ok", 2)


async def test_a_second_garbled_tool_call_is_raised() -> None:
    failure = ToolUseFailedError(400, "bad call", "tool_use_failed")
    llm = ScriptedLLM([failure, ToolUseFailedError(400, "bad again", "tool_use_failed")])
    with pytest.raises(ToolUseFailedError):
        await agent(llm).ask("question")
