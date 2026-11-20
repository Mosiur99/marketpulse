"""marketpulse-eval: scores docs search (retrieval) and the agent's answers (answers)."""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from marketpulse.agent.llm import OpenAICompatibleClient
from marketpulse.agent.loop import Agent, AgentResult
from marketpulse.config import Settings, get_settings
from marketpulse.db import create_engine
from marketpulse.docs.embedder import FastEmbedEmbedder
from marketpulse.docs.search import DocSearch, Mode
from marketpulse.evals.answers import (
    CaseResult,
    RecordingToolBox,
    answers_report,
    load_cases,
    run_cases,
)
from marketpulse.evals.retrieval import (
    ModeReport,
    evaluate,
    load_baseline,
    load_golden,
    markdown_table,
    regressions,
    save_baseline,
    section_of,
)
from marketpulse.evals.snapshot import SnapshotMarket, load_snapshot

EVALS = Path("evals")
GOLDEN = EVALS / "retrieval.toml"
BASELINE = EVALS / "retrieval-baseline.json"
CASES = EVALS / "answers.toml"
SNAPSHOT = EVALS / "market-snapshot.json"


async def run_retrieval(settings: Settings, k: int) -> list[ModeReport]:
    engine = create_engine(settings)
    try:
        search = DocSearch(
            engine, FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
        )

        async def sections(question: str, limit: int, mode: Mode) -> list[str]:
            return [section_of(hit.url) for hit in await search.search(question, limit, mode)]

        return await evaluate(sections, load_golden(GOLDEN), k)
    finally:
        await engine.dispose()


def retrieval(args: argparse.Namespace) -> int:
    settings = get_settings()
    baseline = load_baseline(BASELINE) if BASELINE.exists() else None
    k = baseline.k if baseline else args.k
    if args.check and baseline and settings.docs_branch != baseline.docs_commit:
        print(
            f"The baseline was measured on docs commit {baseline.docs_commit}; set "
            f"MARKETPULSE_DOCS_BRANCH to it and ingest again (now: {settings.docs_branch}).",
            file=sys.stderr,
        )
        return 2
    reports = asyncio.run(run_retrieval(settings, k))
    print(markdown_table(reports, load_golden(GOLDEN), k))
    for report in reports:
        for question in report.misses:
            print(f"  {report.mode} missed: {question}")
    if args.write_baseline:
        save_baseline(BASELINE, settings.docs_branch, k, reports)
        print(f"Baseline written to {BASELINE}")
    if args.check:
        if baseline is None:
            print(f"No baseline at {BASELINE}; run with --write-baseline first.", file=sys.stderr)
            return 2
        problems = regressions(reports, baseline)
        for problem in problems:
            print(f"REGRESSION: {problem}", file=sys.stderr)
        return 1 if problems else 0
    return 0


async def run_answers(
    settings: Settings, api_key: str, delay: float, seconds_per_case: float
) -> list[CaseResult]:
    snapshot = load_snapshot(SNAPSHOT)
    market = SnapshotMarket(snapshot)
    engine = create_engine(settings)
    try:
        docs = DocSearch(
            engine, FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
        )
        async with httpx.AsyncClient(timeout=seconds_per_case) as http:
            llm = OpenAICompatibleClient(http, settings.llm_base_url, api_key, settings.llm_model)

            async def ask(question: str) -> tuple[AgentResult, list[str]]:
                tools = RecordingToolBox(
                    market=market,
                    docs=docs,
                    symbols=list(snapshot.candles),
                    clock=lambda: snapshot.now,
                )
                agent = Agent(
                    llm,
                    tools,
                    tools.clock,
                    max_steps=settings.agent_max_steps,
                    timeout_seconds=seconds_per_case,
                )
                return await agent.ask(question), tools.outputs

            return await run_cases(load_cases(CASES), ask, delay, asyncio.sleep)
    finally:
        await engine.dispose()


def answers(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.llm_api_key is None:
        print("Set MARKETPULSE_LLM_API_KEY to run the answer eval.", file=sys.stderr)
        return 2
    results = asyncio.run(
        run_answers(settings, settings.llm_api_key.get_secret_value(), args.delay, args.timeout)
    )
    print(answers_report(results, settings.llm_model))
    passed = sum(r.grade.passed for r in results) / len(results)
    return 1 if args.min_pass is not None and passed < args.min_pass else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    retrieval_parser = commands.add_parser("retrieval", help="score docs search")
    retrieval_parser.add_argument(
        "--k", type=int, default=5, help="results to look at per question"
    )
    retrieval_parser.add_argument(
        "--check", action="store_true", help="exit 1 when a score falls below the baseline"
    )
    retrieval_parser.add_argument(
        "--write-baseline", action="store_true", help="save these scores as the new baseline"
    )
    answers_parser = commands.add_parser("answers", help="run the agent on the answer cases")
    answers_parser.add_argument(
        "--delay", type=float, default=20, help="seconds between cases (free-tier rate limits)"
    )
    # Longer than the service's limit: a rate-limited call may wait up to 30 s, twice.
    answers_parser.add_argument("--timeout", type=float, default=180, help="seconds per case")
    answers_parser.add_argument(
        "--min-pass", type=float, help="exit 1 when the share of passed cases is lower"
    )
    args = parser.parse_args()
    sys.exit(retrieval(args) if args.command == "retrieval" else answers(args))
