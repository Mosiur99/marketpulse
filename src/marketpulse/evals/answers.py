"""Answer evaluation: runs the agent on fixed cases and grades answers by rule, no AI judge."""

import json
import re
import tomllib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from marketpulse.agent.llm import LLMError, RateLimitedError
from marketpulse.agent.loop import AgentResult
from marketpulse.agent.tools import ToolBox
from marketpulse.evals.retrieval import section_of

# Removed before reading figures: links, paths, dates and clock times hold digits that are not data.
NOT_FIGURES = re.compile(
    r"https?://\S+|\S*/\S*|\d{4}-\d{2}-\d{2}(?:[ T][\d:.+]+)?|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)
FIGURE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?:\s?[kKM]\b)?")
# gpt-oss wraps citations in 【 】, so those brackets end a link too.
URL = re.compile(r"https?://[^\s)\]>\"'`【】「」]+")
# Whole numbers this small are counts, windows and steps ("last 3 candles", "15 minutes"), not data.
SMALL = 60
NO_DATA = re.compile(
    r"not (?:currently )?(?:tracked|available|supported|covered)"
    r"|no (?:data|trades|record|information)"
    r"|(?:don't|do not|doesn't|does not|can't|cannot|unable to) "
    r"(?:have|track|provide|access|retrieve|find)"
    r"|only (?:track|cover|have)",
    re.IGNORECASE,
)
MULTIPLIERS = {"k": Decimal(1_000), "K": Decimal(1_000), "M": Decimal(1_000_000)}


@dataclass(frozen=True)
class Case:
    question: str
    tools: tuple[str, ...] = ()
    figures: tuple[str, ...] = ()
    sections: frozenset[str] = frozenset()
    no_data: bool = False


def load_cases(path: Path) -> list[Case]:
    return [
        Case(
            question=item["q"],
            tools=tuple(item.get("tools", ())),
            figures=tuple(item.get("figures", ())),
            sections=frozenset(item.get("sections", ())),
            no_data=item.get("no_data", False),
        )
        for item in tomllib.loads(path.read_text())["case"]
    ]


@dataclass(frozen=True)
class Figure:
    value: Decimal
    decimals: int


# Models write dashes and minus signs as Unicode, and may group thousands with narrow spaces.
DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2212": "-"})
DIGIT_GROUP_SPACE = re.compile(r"(?<=\d)[\u00a0\u202f\u2009](?=\d{3}\b)")


def figures(text: str, skip_dates_and_links: bool = True) -> list[Figure]:
    found = []
    text = DIGIT_GROUP_SPACE.sub("", text.translate(DASHES))
    if skip_dates_and_links:
        text = NOT_FIGURES.sub(" ", text)
    for match in FIGURE.finditer(text):
        raw = match.group().replace(",", "").replace(" ", "")
        multiplier = MULTIPLIERS.get(raw[-1], Decimal(1))
        number = raw.rstrip("kKM")
        try:
            value = Decimal(number)
        except InvalidOperation:
            continue
        decimals = len(number.split(".")[1]) if "." in number else 0
        if multiplier != 1:
            # 63.1k is 63,100 written to the nearest hundred.
            decimals -= len(str(multiplier)) - 1
        found.append(Figure(value * multiplier, decimals))
    return found


def matches(said: Figure, actual: Decimal) -> bool:
    """`said` is `actual` written to fewer decimals, either sign ("fell 0.93%" for -0.9263)."""
    step = Decimal(10) ** -said.decimals
    return any(abs(said.value - candidate) <= step / 2 for candidate in (actual, -actual))


def unsupported(answer: str, question: str, tool_outputs: Sequence[str]) -> list[str]:
    """Figures in the answer that neither the tools nor the question contain. Dates in tool
    output count too, so "on 19 November 2026" is grounded."""
    known = [f.value for text in [question, *tool_outputs] for f in figures(text, False)]
    return [
        f"{f.value}"
        for f in figures(answer)
        if not (f.decimals == 0 and abs(f.value) <= SMALL)
        and not any(matches(f, value) for value in known)
    ]


def cited_sections(answer: str) -> set[str]:
    return {section_of(url.rstrip(".,;:")) for url in URL.findall(answer)}


@dataclass
class Grade:
    checks: dict[str, bool]
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def grade(case: Case, result: AgentResult, tool_outputs: Sequence[str]) -> Grade:
    answer = result.answer or ""
    checks = {"answered": result.status == "answered"}
    notes = [] if checks["answered"] else [f"status {result.status}"]

    stray = unsupported(answer, case.question, tool_outputs)
    checks["grounded"] = not stray
    if stray:
        notes.append(f"figures not from the tools: {', '.join(stray)}")

    if case.tools:
        used = {call.name for call in result.calls if call.ok}
        missing = [name for name in case.tools if name not in used]
        checks["tools"] = not missing
        if missing:
            notes.append(f"did not call {', '.join(missing)}")

    if case.figures:
        said = figures(answer)
        absent = [f for f in case.figures if not any(matches(s, Decimal(f)) for s in said)]
        checks["figures"] = not absent
        if absent:
            notes.append(f"missing {', '.join(absent)}")

    if case.sections:
        cited = cited_sections(answer)
        checks["sources"] = bool(cited & case.sections)
        if not checks["sources"]:
            notes.append(f"cited {sorted(cited) or 'nothing'}")

    if case.no_data:
        checks["no_data"] = bool(NO_DATA.search(answer.replace("\u2019", "'")))
        if not checks["no_data"]:
            notes.append("did not say the data is missing")
    return Grade(checks, notes)


@dataclass
class RecordingToolBox(ToolBox):
    """Keeps every tool output so figures in the answer can be traced back to them."""

    outputs: list[str] = field(default_factory=list)

    async def run(self, name: str, arguments: str) -> str:
        output = await super().run(name, arguments)
        self.outputs.append(output)
        return output


@dataclass
class CaseResult:
    case: Case
    result: AgentResult | None
    grade: Grade

    @property
    def tokens(self) -> int:
        return self.result.prompt_tokens + self.result.completion_tokens if self.result else 0


Ask = Callable[[str], Awaitable[tuple[AgentResult, list[str]]]]


async def run_cases(
    cases: Sequence[Case],
    ask: Ask,
    delay_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
    rate_limit_wait: float = 60.0,
) -> list[CaseResult]:
    """Runs the cases one by one, pausing between them to stay inside free-tier rate limits.
    A case that hits the rate limit waits a minute and runs once more."""
    results = []
    for index, case in enumerate(cases):
        if index:
            await sleep(delay_seconds)
        try:
            try:
                result, outputs = await ask(case.question)
            except RateLimitedError:
                await sleep(rate_limit_wait)
                result, outputs = await ask(case.question)
        except LLMError as error:
            results.append(CaseResult(case, None, Grade({"answered": False}, [str(error)])))
            continue
        results.append(CaseResult(case, result, grade(case, result, outputs)))
    return results


def answers_json(results: Sequence[CaseResult]) -> str:
    """Every answer with its verdict, for reading what the model actually said."""
    return json.dumps(
        [
            {
                "question": r.case.question,
                "passed": r.grade.passed,
                "status": r.result.status if r.result else None,
                "answer": r.result.answer if r.result else None,
                "tools": [c.name for c in r.result.calls] if r.result else [],
                "notes": r.grade.notes,
            }
            for r in results
        ],
        indent=2,
        ensure_ascii=False,
    )


def answers_report(results: Sequence[CaseResult], model: str) -> str:
    passed = sum(r.grade.passed for r in results)
    lines = [
        f"**{passed} of {len(results)} answers passed** with `{model}`",
        "",
        "| # | Question | Result | Tokens | Seconds | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for number, r in enumerate(results, start=1):
        seconds = f"{r.result.latency_ms / 1000:.1f}" if r.result else "-"
        verdict = "pass" if r.grade.passed else "FAIL"
        notes = "; ".join(r.grade.notes).replace("|", "/")
        lines.append(
            f"| {number} | {r.case.question} | {verdict} | {r.tokens} | {seconds} | {notes} |"
        )
    names = sorted({name for r in results for name in r.grade.checks})
    rates = []
    for name in names:
        graded = [r.grade.checks[name] for r in results if name in r.grade.checks]
        rates.append(f"{name} {sum(graded)}/{len(graded)}")
    total = sum(r.tokens for r in results)
    lines += ["", f"Checks: {', '.join(rates)}. Tokens: {total:,}. Cost: $0 (free tier)."]
    return "\n".join(lines)
