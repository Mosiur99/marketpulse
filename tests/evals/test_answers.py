import json
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from marketpulse.agent.llm import LLMError, RateLimitedError
from marketpulse.agent.loop import Agent, AgentResult
from marketpulse.agent.tools import CallRecord
from marketpulse.docs.search import DocHit, DocSearch
from marketpulse.evals.answers import (
    Case,
    Figure,
    RecordingToolBox,
    answers_report,
    cited_sections,
    figures,
    grade,
    load_cases,
    matches,
    run_cases,
    unsupported,
)
from marketpulse.evals.snapshot import SnapshotMarket, load_snapshot
from tests.agent.fakes import ScriptedLLM, answers, asks_for, call

EVALS = Path(__file__).parents[2] / "evals"
SNAPSHOT = load_snapshot(EVALS / "market-snapshot.json")
DOC_URL = (
    "https://github.com/binance/binance-spot-api-docs/blob/abc/errors.md#-1021-invalid_timestamp"
)


def values(text: str) -> list[Decimal]:
    return [f.value for f in figures(text)]


def test_figures_read_prices_percentages_and_thousands() -> None:
    assert figures("BTC is $63,103.30, down -0.93%") == [
        Figure(Decimal("63103.30"), 2),
        Figure(Decimal("-0.93"), 2),
    ]
    assert figures("about 63.1k") == [Figure(Decimal("63100.0"), -2)]


def test_figures_read_unicode_minus_signs_and_narrow_space_groups() -> None:
    assert values("down \u22120.93%, at 63\u202f103.30 on 2026\u201111\u201119") == [
        Decimal("-0.93"),
        Decimal("63103.30"),
    ]


def test_figures_skip_dates_times_links_and_paths() -> None:
    text = f"On 2026-11-19 at 15:59 UTC, GET /api/v3/klines, see {DOC_URL}. Version v3."
    assert values(text) == []


def test_matches_allows_rounding_and_either_sign() -> None:
    assert matches(Figure(Decimal("-0.93"), 2), Decimal("-0.9263"))
    assert matches(Figure(Decimal("0.93"), 2), Decimal("-0.9263"))
    assert matches(Figure(Decimal("63103"), 0), Decimal("63103.3"))
    assert matches(Figure(Decimal("63100"), -2), Decimal("63103.3"))
    assert not matches(Figure(Decimal("0.92"), 2), Decimal("-0.9263"))
    assert not matches(Figure(Decimal("63000"), 0), Decimal("63103.3"))


def test_unsupported_flags_figures_the_tools_did_not_return() -> None:
    outputs = ['{"price": "63103.3", "traded_at": "2026-11-19 15:59:59+00:00"}']
    assert unsupported("BTC is at $63,103.30 (19 Nov 2026), last 5 minutes", "BTC?", outputs) == []
    assert unsupported("BTC is at 64,250 and ETH at 1,800.5", "BTC?", outputs) == [
        "64250",
        "1800.5",
    ]
    assert unsupported("Back in 2020 it was low.", "price on January 1, 2020?", []) == []


def test_cited_sections_read_links_in_text_and_markdown() -> None:
    assert cited_sections(f"See [the docs]({DOC_URL}).") == {"errors.md#-1021-invalid_timestamp"}
    assert cited_sections(f"Source: {DOC_URL}.") == {"errors.md#-1021-invalid_timestamp"}
    assert cited_sections("no link") == set()


def result(answer: str, *calls: str, status: str = "answered") -> AgentResult:
    return AgentResult(
        status=status,  # type: ignore[arg-type]
        answer=answer,
        steps=2,
        calls=[CallRecord(name, "{}", True) for name in calls],
    )


def test_a_correct_answer_passes_every_check() -> None:
    case = Case("BTC price?", tools=("get_price",), figures=("63103.3",))
    outputs = ['{"price": "63103.3"}']
    verdict = grade(case, result("BTC trades at $63,103.30.", "get_price"), outputs)
    assert verdict.passed
    assert verdict.checks == {"answered": True, "grounded": True, "tools": True, "figures": True}


def test_each_check_can_fail_with_a_note() -> None:
    case = Case("BTC price?", tools=("get_price",), figures=("63103.3",))
    verdict = grade(case, result("BTC is about 70,000."), [])
    assert verdict.checks == {"answered": True, "grounded": False, "tools": False, "figures": False}
    assert verdict.notes == [
        "figures not from the tools: 70000",
        "did not call get_price",
        "missing 63103.3",
    ]
    assert not grade(case, result("", status="step_limit"), []).checks["answered"]


def test_sources_must_be_linked_in_the_answer() -> None:
    case = Case("-1021?", sections=frozenset({"errors.md#-1021-invalid_timestamp"}))
    assert grade(case, result(f"A timestamp outside recvWindow. {DOC_URL}"), []).passed
    missing = grade(case, result("A timestamp outside recvWindow."), [])
    assert missing.checks["sources"] is False
    assert missing.notes == ["cited nothing"]


@pytest.mark.parametrize(
    "answer",
    [
        "DOGE is not tracked; I only track BTCUSDT, ETHUSDT and SOLUSDT.",
        "I don't have price data from 2020.",
        "There is no data for that date.",
    ],
)
def test_no_data_answers_pass(answer: str) -> None:
    case = Case("DOGE price?", no_data=True)
    assert grade(
        case,
        result(answer),
        [],
    ).checks["no_data"]


def test_a_made_up_price_fails_a_no_data_case() -> None:
    verdict = grade(Case("DOGE price?", no_data=True), result("DOGE is at 0.38."), [])
    assert verdict.checks["no_data"] is False
    assert verdict.checks["grounded"] is False


def test_the_committed_cases_are_well_formed() -> None:
    cases = load_cases(EVALS / "answers.toml")
    assert len(cases) == 15
    assert sum(case.no_data for case in cases) == 2
    for case in cases:
        assert case.tools or case.no_data, case.question
        for figure in case.figures:
            Decimal(figure)
        assert all("#" in section for section in case.sections)


async def test_run_cases_pauses_and_retries_once_after_a_rate_limit() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    attempts: list[str] = []

    async def ask(question: str) -> tuple[AgentResult, list[str]]:
        attempts.append(question)
        if question == "slow" and attempts.count("slow") == 1:
            raise RateLimitedError(429, "slow down")
        if question == "broken":
            raise LLMError(500, "provider down")
        return result("I don't have that data."), []

    cases = [Case("slow", no_data=True), Case("broken"), Case("fine", no_data=True)]
    results = await run_cases(cases, ask, delay_seconds=20, sleep=sleep, rate_limit_wait=60)
    assert attempts == ["slow", "slow", "broken", "fine"]
    assert slept == [60, 20, 20]
    assert [r.grade.passed for r in results] == [True, False, True]
    assert results[1].grade.notes == ["500: provider down"]


class FakeDocs:
    async def search(self, query: str, limit: int = 5, mode: str = "hybrid") -> list[DocHit]:
        return [DocHit(1, "Errors > -1021 INVALID_TIMESTAMP", DOC_URL, "Timestamp outside.", 1.0)]


def toolbox() -> RecordingToolBox:
    return RecordingToolBox(
        market=SnapshotMarket(SNAPSHOT),
        docs=cast(DocSearch, FakeDocs()),
        symbols=list(SNAPSHOT.candles),
        clock=lambda: SNAPSHOT.now,
    )


async def test_end_to_end_with_a_scripted_model_and_the_snapshot() -> None:
    tools = toolbox()
    llm = ScriptedLLM(
        [
            asks_for(call("get_price", {"symbol": "BTCUSDT"})),
            answers("BTC last traded at $63,103.30 at 15:59 UTC on 2026-11-19."),
        ]
    )
    agent_result = await Agent(llm, tools, tools.clock).ask("What is the latest BTC price?")
    assert json.loads(tools.outputs[0])["price"] == "63103.3"
    case = Case("What is the latest BTC price?", tools=("get_price",), figures=("63103.3",))
    verdict = grade(case, agent_result, tools.outputs)
    assert verdict.passed, verdict.notes


async def test_end_to_end_a_hallucinated_figure_is_caught() -> None:
    tools = toolbox()
    llm = ScriptedLLM([answers("BTC is at $65,000 right now.")])
    agent_result = await Agent(llm, tools, tools.clock).ask("What is the latest BTC price?")
    case = Case("What is the latest BTC price?", tools=("get_price",), figures=("63103.3",))
    verdict = grade(case, agent_result, tools.outputs)
    assert verdict.checks == {"answered": True, "grounded": False, "tools": False, "figures": False}


def test_report_lists_each_case_and_totals() -> None:
    from marketpulse.evals.answers import CaseResult, Grade

    ok = AgentResult("answered", "x", 2, prompt_tokens=900, completion_tokens=100, latency_ms=2500)
    rows = [
        CaseResult(Case("BTC price?"), ok, Grade({"answered": True, "figures": True})),
        CaseResult(Case("DOGE?"), None, Grade({"answered": False}, ["provider down"])),
    ]
    text = answers_report(rows, "openai/gpt-oss-120b")
    assert text.splitlines()[0] == "**1 of 2 answers passed** with `openai/gpt-oss-120b`"
    assert "| 1 | BTC price? | pass | 1000 | 2.5 |  |" in text
    assert "| 2 | DOGE? | FAIL | 0 | - | provider down |" in text
    assert text.endswith("Checks: answered 1/2, figures 1/1. Tokens: 1,000. Cost: $0 (free tier).")
