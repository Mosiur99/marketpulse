import pytest

from marketpulse.docs.search import chunk_url, fused_order, reciprocal_rank_fusion


def test_ids_found_by_both_lists_rank_first() -> None:
    scores = reciprocal_rank_fusion([[1, 2, 3], [3, 4]], k=60)
    assert max(scores, key=lambda key: scores[key]) == 3
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)


def test_a_single_list_keeps_its_order() -> None:
    scores = reciprocal_rank_fusion([[7, 8, 9]])
    assert sorted(scores, key=lambda key: -scores[key]) == [7, 8, 9]


def test_no_results() -> None:
    assert reciprocal_rank_fusion([[], []]) == {}


def test_chunk_urls_point_at_the_heading_or_the_page() -> None:
    assert chunk_url("https://x/doc.md", "lot_size") == "https://x/doc.md#lot_size"
    assert chunk_url("https://x/doc.md", "") == "https://x/doc.md"


def test_a_tie_goes_to_the_chunk_the_last_list_ranks_higher() -> None:
    vector, keyword = [1, 2], [2, 1]
    scores = reciprocal_rank_fusion([vector, keyword])
    assert scores[1] == scores[2]
    assert fused_order(scores, keyword) == [2, 1]


def test_fused_order_puts_the_best_score_first_regardless_of_the_tie_breaker() -> None:
    assert fused_order({1: 0.5, 2: 0.9, 3: 0.1}, [3, 1]) == [2, 1, 3]
