import pytest

from marketpulse.docs.search import chunk_url, reciprocal_rank_fusion


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
