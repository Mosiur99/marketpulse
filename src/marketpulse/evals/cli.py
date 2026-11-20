"""marketpulse-eval: scores docs search on the golden question set."""

import argparse
import asyncio
import sys
from pathlib import Path

from marketpulse.config import Settings, get_settings
from marketpulse.db import create_engine
from marketpulse.docs.embedder import FastEmbedEmbedder
from marketpulse.docs.search import DocSearch, Mode
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

EVALS = Path("evals")
GOLDEN = EVALS / "retrieval.toml"
BASELINE = EVALS / "retrieval-baseline.json"


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
    args = parser.parse_args()
    sys.exit(retrieval(args))
