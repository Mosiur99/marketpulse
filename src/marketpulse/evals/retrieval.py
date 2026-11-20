"""Retrieval evaluation: does search put a correct doc section in the top k for each question?"""

import json
import tomllib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from marketpulse.docs.search import Mode

MODES: tuple[Mode, ...] = ("vector", "keyword", "hybrid")
# Scores are deterministic for a pinned docs commit: losing one question of forty fails the check.
TOLERANCE = 0.02


Kind = Literal["natural", "exact"]
KINDS: tuple[Kind, ...] = ("natural", "exact")


@dataclass(frozen=True)
class GoldenQuestion:
    question: str
    expect: frozenset[str]
    kind: Kind = "natural"


@dataclass(frozen=True)
class Score:
    hit_rate: float
    mrr: float


@dataclass(frozen=True)
class Baseline:
    docs_commit: str
    k: int
    scores: dict[str, Score]


Search = Callable[[str, int, Mode], Awaitable[list[str]]]


def load_golden(path: Path) -> list[GoldenQuestion]:
    data = tomllib.loads(path.read_text())
    return [
        GoldenQuestion(item["q"], frozenset(item["expect"]), item["kind"])
        for item in data["question"]
    ]


def section_of(url: str) -> str:
    """https://github.com/org/repo/blob/<ref>/rest-api.md#limits -> rest-api.md#limits"""
    return url.rsplit("/", 1)[-1]


def first_hit(found: Sequence[str], expect: frozenset[str]) -> int | None:
    """1-based rank of the first correct section, or None when none was found."""
    return next((rank for rank, section in enumerate(found, start=1) if section in expect), None)


def score(ranks: Sequence[int | None]) -> Score:
    if not ranks:
        return Score(0.0, 0.0)
    hits = [rank for rank in ranks if rank is not None]
    return Score(
        hit_rate=round(len(hits) / len(ranks), 4),
        mrr=round(sum(1 / rank for rank in hits) / len(ranks), 4),
    )


@dataclass(frozen=True)
class ModeReport:
    mode: Mode
    score: Score
    by_kind: dict[Kind, Score]
    misses: list[str]


async def evaluate(
    search: Search, golden: Sequence[GoldenQuestion], k: int, modes: Sequence[Mode] = MODES
) -> list[ModeReport]:
    reports = []
    for mode in modes:
        ranks: dict[Kind, list[int | None]] = {kind: [] for kind in KINDS}
        misses = []
        for item in golden:
            rank = first_hit(await search(item.question, k, mode), item.expect)
            ranks[item.kind].append(rank)
            if rank is None:
                misses.append(item.question)
        every = [rank for kind in KINDS for rank in ranks[kind]]
        by_kind = {kind: score(ranks[kind]) for kind in KINDS if ranks[kind]}
        reports.append(ModeReport(mode, score(every), by_kind, misses))
    return reports


def regressions(reports: Sequence[ModeReport], baseline: Baseline) -> list[str]:
    """Every metric that fell more than TOLERANCE below the committed baseline."""
    problems = []
    for report in reports:
        expected = baseline.scores.get(report.mode)
        if expected is None:
            continue
        for metric in ("hit_rate", "mrr"):
            now, then = getattr(report.score, metric), getattr(expected, metric)
            if now < then - TOLERANCE:
                problems.append(f"{report.mode} {metric} fell from {then} to {now}")
    return problems


def load_baseline(path: Path) -> Baseline:
    data = json.loads(path.read_text())
    return Baseline(
        docs_commit=data["docs_commit"],
        k=data["k"],
        scores={mode: Score(**values) for mode, values in data["scores"].items()},
    )


def save_baseline(path: Path, docs_commit: str, k: int, reports: Sequence[ModeReport]) -> None:
    data = {
        "docs_commit": docs_commit,
        "k": k,
        "scores": {report.mode: asdict(report.score) for report in reports},
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def markdown_table(reports: Sequence[ModeReport], golden: Sequence[GoldenQuestion], k: int) -> str:
    counts = {kind: sum(item.kind == kind for item in golden) for kind in KINDS}
    columns = [f"all {len(golden)}"] + [f"{kind} {counts[kind]}" for kind in KINDS if counts[kind]]
    lines = [
        f"| Search | Hit rate @{k} ({', '.join(columns)}) | MRR @{k} ({', '.join(columns)}) |",
        "|---|---|---|",
    ]
    for report in reports:
        scores = [report.score] + [report.by_kind[kind] for kind in KINDS if counts[kind]]
        hits = " / ".join(f"{s.hit_rate:.2f}" for s in scores)
        mrrs = " / ".join(f"{s.mrr:.2f}" for s in scores)
        lines.append(f"| {report.mode} | {hits} | {mrrs} |")
    return "\n".join(lines)
