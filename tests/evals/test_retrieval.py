import re
from pathlib import Path

import pytest

from marketpulse.docs.search import Mode
from marketpulse.evals.retrieval import (
    Baseline,
    GoldenQuestion,
    ModeReport,
    Score,
    evaluate,
    first_hit,
    load_baseline,
    load_golden,
    markdown_table,
    regressions,
    save_baseline,
    score,
    section_of,
)

GOLDEN = Path(__file__).parents[2] / "evals" / "retrieval.toml"
SECTION = re.compile(r"^[a-z-]+\.md#[a-z0-9_-]+$")


def test_golden_set_is_well_formed() -> None:
    golden = load_golden(GOLDEN)
    assert len(golden) == 40
    assert {item.kind for item in golden} == {"natural", "exact"}
    assert len({item.question for item in golden}) == len(golden)
    for item in golden:
        assert item.expect, item.question
        assert all(SECTION.match(section) for section in item.expect), item.expect


def test_section_of_a_github_url() -> None:
    url = "https://github.com/binance/binance-spot-api-docs/blob/473c101/errors.md#-1021-invalid_timestamp"
    assert section_of(url) == "errors.md#-1021-invalid_timestamp"


def test_first_hit_is_one_based_and_none_when_missing() -> None:
    assert first_hit(["a", "b", "c"], frozenset({"b", "c"})) == 2
    assert first_hit(["a"], frozenset({"z"})) is None


def test_score_counts_hits_and_reciprocal_ranks() -> None:
    assert score([1, 2, None, 4]) == Score(hit_rate=0.75, mrr=round((1 + 0.5 + 0.25) / 4, 4))
    assert score([]) == Score(0.0, 0.0)


async def test_evaluate_scores_each_mode_and_kind_and_lists_misses() -> None:
    golden = [
        GoldenQuestion("klines weight", frozenset({"rest-api.md#klines"}), "natural"),
        GoldenQuestion("-1021", frozenset({"errors.md#-1021"}), "exact"),
    ]
    found = {
        "vector": {"klines weight": ["rest-api.md#klines"], "-1021": ["errors.md#-1000"]},
        "keyword": {"klines weight": ["x", "rest-api.md#klines"], "-1021": ["errors.md#-1021"]},
    }
    seen_limits = []

    async def search(question: str, limit: int, mode: Mode) -> list[str]:
        seen_limits.append(limit)
        return found[mode][question]

    vector, keyword = await evaluate(search, golden, k=5, modes=("vector", "keyword"))
    assert vector.score == Score(0.5, 0.5)
    assert vector.by_kind == {"natural": Score(1.0, 1.0), "exact": Score(0.0, 0.0)}
    assert vector.misses == ["-1021"]
    assert keyword.score == Score(1.0, 0.75)
    assert keyword.misses == []
    assert set(seen_limits) == {5}


def report(mode: Mode, hit_rate: float, mrr: float) -> ModeReport:
    return ModeReport(mode, Score(hit_rate, mrr), {"natural": Score(hit_rate, mrr)}, [])


def test_regressions_flag_drops_beyond_the_tolerance_only() -> None:
    baseline = Baseline("abc", 5, {"hybrid": Score(0.9, 0.75), "vector": Score(0.9, 0.8)})
    assert regressions([report("hybrid", 0.89, 0.74), report("vector", 0.95, 0.9)], baseline) == []
    assert regressions([report("hybrid", 0.875, 0.75)], baseline) == [
        "hybrid hit_rate fell from 0.9 to 0.875"
    ]
    assert regressions([report("keyword", 0.1, 0.1)], baseline) == []


def test_baseline_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    save_baseline(path, "473c101", 5, [report("hybrid", 0.9, 0.755)])
    assert load_baseline(path) == Baseline("473c101", 5, {"hybrid": Score(0.9, 0.755)})


def test_committed_baseline_covers_every_mode() -> None:
    baseline = load_baseline(GOLDEN.with_name("retrieval-baseline.json"))
    assert set(baseline.scores) == {"vector", "keyword", "hybrid"}
    assert re.fullmatch(r"[0-9a-f]{40}", baseline.docs_commit)


def test_markdown_table_shows_all_and_each_kind() -> None:
    golden = [
        GoldenQuestion("a", frozenset({"x.md#a"}), "natural"),
        GoldenQuestion("b", frozenset({"x.md#b"}), "exact"),
    ]
    hybrid = ModeReport(
        "hybrid", Score(0.5, 0.25), {"natural": Score(1.0, 0.5), "exact": Score(0.0, 0.0)}, []
    )
    table = markdown_table([hybrid], golden, 5).splitlines()
    columns = "(all 2, natural 1, exact 1)"
    assert table[0] == f"| Search | Hit rate @5 {columns} | MRR @5 {columns} |"
    assert table[2] == "| hybrid | 0.50 / 1.00 / 0.00 | 0.25 / 0.50 / 0.00 |"


@pytest.mark.parametrize("kind", ["natural", "exact"])
def test_every_kind_has_questions(kind: str) -> None:
    assert any(item.kind == kind for item in load_golden(GOLDEN))
