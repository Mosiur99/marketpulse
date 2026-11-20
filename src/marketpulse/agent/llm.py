"""A small client for any OpenAI-compatible chat completions API (Groq by default)."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

Message = dict[str, Any]
MAX_WAIT_SECONDS = 30.0


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Completion:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_message(self) -> Message:
        message: Message = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class LLMError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.code = code


class RateLimitedError(LLMError):
    """The provider still refused after waiting as long as it asked."""


class ToolUseFailedError(LLMError):
    """The model wrote a tool call the provider could not parse; asking again usually works."""


class LLMClient(Protocol):
    model: str

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[Message]
    ) -> Completion: ...


def parse_completion(body: dict[str, Any]) -> Completion:
    choice = body["choices"][0]
    message = choice["message"]
    usage = body.get("usage") or {}
    return Completion(
        content=message.get("content"),
        tool_calls=[
            ToolCall(call["id"], call["function"]["name"], call["function"]["arguments"] or "{}")
            for call in message.get("tool_calls") or []
        ],
        finish_reason=choice.get("finish_reason") or "stop",
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
    )


def retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        seconds = 2.0**attempt
    return min(max(seconds, 0.0), MAX_WAIT_SECONDS)


class OpenAICompatibleClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str,
        max_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model
        self._max_retries = max_retries
        self._sleep = sleep

    async def complete(self, messages: Sequence[Message], tools: Sequence[Message]) -> Completion:
        payload = {"model": self.model, "messages": list(messages), "tools": list(tools)}
        for attempt in range(self._max_retries + 1):
            response = await self._http.post(self._url, json=payload, headers=self._headers)
            if response.is_success:
                return parse_completion(response.json())
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self._max_retries:
                await self._sleep(retry_after(response, attempt))
                continue
            raise error_for(response)
        raise AssertionError("unreachable")


def error_for(response: httpx.Response) -> LLMError:
    try:
        error = response.json().get("error") or {}
    except json.JSONDecodeError:
        error = {}
    message = str(error.get("message") or response.text[:200] or response.reason_phrase)
    code = error.get("code")
    if response.status_code == 429:
        return RateLimitedError(429, message, code)
    if code == "tool_use_failed":
        return ToolUseFailedError(response.status_code, message, code)
    return LLMError(response.status_code, message, code)
