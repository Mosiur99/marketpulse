"""Splits Markdown into heading-aware chunks small enough to embed."""

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
TOC_LINE = re.compile(r"^\s*[-*+]\s+\[.*\]\(#.*\)\s*$")
TOKEN = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
    """Approximates the embedding model's token count: words and punctuation marks."""
    return len(TOKEN.findall(text))


@dataclass(frozen=True)
class Section:
    heading_path: str
    anchor: str
    body: str


@dataclass(frozen=True)
class Chunk:
    heading_path: str
    anchor: str
    content: str

    @property
    def content_hash(self) -> str:
        key = f"{self.heading_path}\n{self.anchor}\n{self.content}"
        return hashlib.sha256(key.encode()).hexdigest()

    @property
    def embedding_text(self) -> str:
        return f"{self.heading_path}\n\n{self.content}" if self.heading_path else self.content


@dataclass
class Slugger:
    """Builds the same heading anchors GitHub does, including -1, -2 for repeats."""

    seen: dict[str, int] = field(default_factory=dict)

    def slug(self, heading: str) -> str:
        base = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        count = self.seen.get(base, 0)
        self.seen[base] = count + 1
        return base if count == 0 else f"{base}-{count}"


def split_sections(markdown: str) -> list[Section]:
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    slugger = Slugger()
    anchor = ""
    body: list[str] = []
    in_fence = False

    def close() -> None:
        sections.append(Section(" > ".join(t for _, t in path), anchor, "\n".join(body).strip()))

    for line in markdown.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else HEADING.match(line)
        if heading is None:
            body.append(line)
            continue
        close()
        level, title = len(heading.group(1)), heading.group(2)
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, title))
        anchor = slugger.slug(title)
        body = []
    close()
    return [s for s in sections if s.body and not is_table_of_contents(s.body)]


def is_table_of_contents(body: str) -> bool:
    lines = [line for line in body.splitlines() if line.strip()]
    return bool(lines) and all(TOC_LINE.match(line) for line in lines)


def blocks(body: str) -> Iterator[str]:
    """Paragraphs and whole fenced code blocks, in order."""
    current: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if FENCE.match(line):
            if not in_fence and current:
                yield "\n".join(current)
                current = []
            current.append(line)
            in_fence = not in_fence
            if not in_fence:
                yield "\n".join(current)
                current = []
        elif in_fence or line.strip():
            current.append(line)
        elif current:
            yield "\n".join(current)
            current = []
    if current:
        yield "\n".join(current)


def split_long_text(block: str, max_tokens: int) -> Iterator[str]:
    """Breaks a paragraph that is too long on its own at line, then word, boundaries."""
    piece: list[str] = []
    size = 0
    for unit in block.splitlines() if "\n" in block else block.split(" "):
        unit_size = count_tokens(unit)
        if piece and size + unit_size > max_tokens:
            yield ("\n" if "\n" in block else " ").join(piece)
            piece, size = [], 0
        piece.append(unit)
        size += unit_size
    if piece:
        yield ("\n" if "\n" in block else " ").join(piece)


def pack(parts: list[str], max_tokens: int, overlap_tokens: int) -> list[str]:
    """Greedily fills chunks; each new chunk repeats the previous chunk's last short paragraphs."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for part in parts:
        part_size = count_tokens(part)
        if current and size + part_size > max_tokens:
            chunks.append(current)
            carried: list[str] = []
            carried_size = 0
            for previous in reversed(current):
                previous_size = count_tokens(previous)
                if FENCE.match(previous) or carried_size + previous_size > overlap_tokens:
                    break
                carried.insert(0, previous)
                carried_size += previous_size
            if carried_size + part_size > max_tokens:
                carried, carried_size = [], 0
            current, size = carried, carried_size
        current.append(part)
        size += part_size
    if current:
        chunks.append(current)
    return ["\n\n".join(chunk) for chunk in chunks]


def chunk_markdown(markdown: str, max_tokens: int = 400, overlap_tokens: int = 60) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in split_sections(markdown):
        parts: list[str] = []
        for block in blocks(section.body):
            if FENCE.match(block) or count_tokens(block) <= max_tokens:
                parts.append(block)
            else:
                parts.extend(split_long_text(block, max_tokens))
        for content in pack(parts, max_tokens, overlap_tokens):
            chunks.append(Chunk(section.heading_path, section.anchor, content))
    return chunks
