import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.agent.loop import AgentResult
from marketpulse.tables import ask_log


class AskLog:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(
        self, question: str, model: str, result: AgentResult | None, error: str | None = None
    ) -> int:
        values: dict[str, Any] = {"question": question, "model": model, "error": error}
        if result is not None:
            values |= {
                "status": result.status,
                "answer": result.answer,
                "steps": result.steps,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "latency_ms": result.latency_ms,
                "tool_calls": json.loads(json.dumps([asdict(c) for c in result.calls])),
            }
        else:
            values |= {"status": "error", "steps": 0, "tool_calls": []}
        async with self._engine.begin() as connection:
            return int(
                await connection.scalar(insert(ask_log).values(**values).returning(ask_log.c.id))
            )
