import time
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from marketpulse.agent.llm import LLMClient, LLMError, RateLimitedError
from marketpulse.agent.log import AskLog
from marketpulse.agent.loop import Agent, Status
from marketpulse.agent.tools import ToolBox
from marketpulse.api.docs import embedder
from marketpulse.api.problems import ProblemError
from marketpulse.docs.search import DocSearch
from marketpulse.market.queries import MarketQueries

router = APIRouter(tags=["agent"])

FALLBACK: dict[Status, str] = {
    "step_limit": "I could not finish within the allowed number of steps.",
    "timeout": "I ran out of time before finishing.",
    "refused": "The model declined to answer this question.",
    "cut_off": "The answer was cut off before it was complete.",
    "answered": "",
}


class Question(BaseModel):
    question: Annotated[str, Field(min_length=3, max_length=500)]


class SourceOut(BaseModel):
    title: str
    url: str


class ToolCallOut(BaseModel):
    name: str
    arguments: str
    ok: bool


class Answer(BaseModel):
    id: int
    status: Status
    answer: str
    sources: list[SourceOut]
    tool_calls: list[ToolCallOut]
    model: str
    steps: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cost: Literal["free tier"] = "free tier"


@router.post("/ask", response_model=Answer)
async def ask(request: Request, body: Question) -> Answer:
    state = request.app.state
    llm: LLMClient | None = state.llm
    if llm is None:
        raise ProblemError(503, "The agent is not configured: set MARKETPULSE_LLM_API_KEY.")
    tools = ToolBox(
        market=MarketQueries(state.engine),
        docs=DocSearch(state.engine, embedder(request)),
        symbols=state.settings.symbols,
        clock=state.clock,
    )
    agent = Agent(
        llm,
        tools,
        state.clock,
        max_steps=state.settings.agent_max_steps,
        timeout_seconds=state.settings.agent_timeout_seconds,
    )
    log = AskLog(state.engine)
    started = time.monotonic()
    try:
        result = await agent.ask(body.question)
    except RateLimitedError as error:
        state.metrics.record_ask("rate_limited", time.monotonic() - started, 0, 0)
        await log.record(body.question, llm.model, None, str(error))
        raise ProblemError(
            503, "The AI provider's free-tier limit was reached. Try again in a minute."
        ) from error
    except LLMError as error:
        state.metrics.record_ask("provider_error", time.monotonic() - started, 0, 0)
        await log.record(body.question, llm.model, None, str(error))
        raise ProblemError(502, f"The AI provider returned an error ({error.status}).") from error
    state.metrics.record_ask(
        result.status, result.latency_ms / 1000, result.prompt_tokens, result.completion_tokens
    )
    answer_id = await log.record(body.question, llm.model, result)
    return Answer(
        id=answer_id,
        status=result.status,
        answer=result.answer or FALLBACK[result.status],
        sources=[SourceOut(title=s.title, url=s.url) for s in result.sources],
        tool_calls=[ToolCallOut(name=c.name, arguments=c.arguments, ok=c.ok) for c in result.calls],
        model=llm.model,
        steps=result.steps,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        latency_ms=result.latency_ms,
    )
