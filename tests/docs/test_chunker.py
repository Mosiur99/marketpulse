from pathlib import Path

from marketpulse.docs.chunker import (
    Slugger,
    chunk_markdown,
    count_tokens,
    split_sections,
)

SAMPLE = (Path(__file__).parent / "fixtures" / "spot-api.md").read_text()


def test_sections_keep_the_full_heading_path() -> None:
    paths = [s.heading_path for s in split_sections(SAMPLE)]
    assert "Exchange API > Market data > Kline/Candlestick data" in paths
    assert "Exchange API > Limits" in paths


def test_anchors_match_github() -> None:
    slugger = Slugger()
    assert slugger.slug("Kline/Candlestick data") == "klinecandlestick-data"
    assert slugger.slug("New OCO - Deprecated (TRADE)") == "new-oco---deprecated-trade"
    assert slugger.slug("Account information (USER_DATA)") == "account-information-user_data"
    assert slugger.slug("Example") == "example"
    assert slugger.slug("Example") == "example-1"


def test_a_hash_inside_a_code_block_is_not_a_heading() -> None:
    sections = split_sections(SAMPLE)
    assert not any("Sign the request" in s.heading_path for s in sections)
    signing = next(s for s in sections if s.heading_path.endswith("Signing requests"))
    assert "# Sign the request" in signing.body


def test_tables_of_contents_and_empty_headings_are_skipped() -> None:
    paths = [s.heading_path for s in split_sections(SAMPLE)]
    assert "Table of Contents" not in paths
    assert "Exchange API > Market data" not in paths


def test_each_endpoint_becomes_one_chunk_with_its_heading() -> None:
    chunks = chunk_markdown(SAMPLE)
    klines = [c for c in chunks if c.anchor == "klinecandlestick-data"]
    assert len(klines) == 1
    assert "GET /api/v3/klines" in klines[0].content
    assert "**Weight:** 2" in klines[0].content
    assert klines[0].embedding_text.startswith("Exchange API > Market data > Kline/Candlestick")


def test_long_sections_are_split_with_overlap() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 30 for i in range(20)]
    chunks = chunk_markdown("## Long\n\n" + "\n\n".join(paragraphs), max_tokens=100)
    assert len(chunks) > 1
    assert all(count_tokens(c.content) <= 100 for c in chunks)
    first_last_paragraph = chunks[0].content.split("\n\n")[-1]
    assert chunks[1].content.startswith(first_last_paragraph)
    assert all(f"Paragraph {i} " in "".join(c.content for c in chunks) for i in range(20))


def test_a_code_block_is_never_split_even_when_too_long() -> None:
    code = "```\n" + "\n".join(f"line {i} = value" for i in range(200)) + "\n```"
    chunks = chunk_markdown(f"## Code\n\nIntro.\n\n{code}\n\nOutro.", max_tokens=50)
    assert sum(code in c.content for c in chunks) == 1
    assert all(c.content.count("```") % 2 == 0 for c in chunks)


def test_one_huge_paragraph_is_split_by_words() -> None:
    chunks = chunk_markdown("## Wall\n\n" + "token " * 500, max_tokens=120)
    assert len(chunks) >= 5
    assert all(count_tokens(c.content) <= 120 for c in chunks)


def test_content_hash_changes_only_when_the_chunk_changes() -> None:
    first = chunk_markdown(SAMPLE)
    again = chunk_markdown(SAMPLE)
    assert [c.content_hash for c in first] == [c.content_hash for c in again]
    edited = chunk_markdown(SAMPLE.replace("**Weight:** 25", "**Weight:** 30"))
    changed = {c.content_hash for c in edited} - {c.content_hash for c in first}
    assert len(changed) == 1
