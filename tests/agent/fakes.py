import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from marketpulse.agent.llm import Completion, LLMError, Message, ToolCall
from marketpulse.agent.tools import CallRecord, Source


def call(name: str, arguments: dict[str, Any] | str, id_: str = "call_1") -> ToolCall:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(id_, name, raw)


def asks_for(*calls: ToolCall, tokens: int = 100) -> Completion:
    return Completion(None, list(calls), "tool_calls", prompt_tokens=tokens, completion_tokens=10)


def answers(text: str, tokens: int = 100) -> Completion:
    return Completion(text, [], "stop", prompt_tokens=tokens, completion_tokens=20)


@dataclass
class ScriptedLLM:
    """Plays back completions (or raises errors) in order and remembers what it was sent."""

    script: list[Completion | LLMError]
    model: str = "fake-model"
    delay: float = 0.0
    sent: list[list[Message]] = field(default_factory=list)

    async def complete(self, messages: Sequence[Message], tools: Sequence[Message]) -> Completion:
        self.sent.append([dict(m) for m in messages])
        if self.delay:
            await asyncio.sleep(self.delay)
        step = self.script.pop(0)
        if isinstance(step, LLMError):
            raise step
        return step


@dataclass
class EchoTools:
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    sources: list[Source] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)

    async def run(self, name: str, arguments: str) -> str:
        self.calls.append(CallRecord(name, arguments, True))
        return json.dumps({"tool": name, "arguments": json.loads(arguments)})
