import json

import httpx
import pytest

from marketpulse.agent.llm import (
    LLMError,
    OpenAICompatibleClient,
    RateLimitedError,
    ToolUseFailedError,
    parse_completion,
)

TOOLS = [{"type": "function", "function": {"name": "get_price", "parameters": {}}}]
MESSAGES = [{"role": "user", "content": "hi"}]


def body(
    content: str | None = "Hi", tool_calls: list[dict[str, object]] | None = None
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


def test_parses_text_and_tool_calls() -> None:
    completion = parse_completion(
        body(
            None,
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_price", "arguments": '{"symbol":"BTCUSDT"}'},
                }
            ],
        )
    )
    assert completion.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in completion.tool_calls] == [
        ("c1", "get_price", '{"symbol":"BTCUSDT"}')
    ]
    assert (completion.prompt_tokens, completion.completion_tokens) == (12, 3)
    assert completion.as_message()["tool_calls"][0]["function"]["name"] == "get_price"


async def run(responses: list[httpx.Response]) -> tuple[object, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    pauses: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses.pop(0)

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAICompatibleClient(http, "https://llm.example/v1/", "secret", "m", sleep=sleep)
        try:
            outcome: object = await client.complete(MESSAGES, TOOLS)
        except LLMError as error:
            outcome = error
    return outcome, requests, pauses


async def test_sends_model_messages_tools_and_key() -> None:
    _, requests, _ = await run([httpx.Response(200, json=body())])
    request = requests[0]
    assert str(request.url) == "https://llm.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    sent = json.loads(request.content)
    assert (sent["model"], sent["messages"], sent["tools"]) == ("m", MESSAGES, TOOLS)


async def test_waits_as_long_as_a_rate_limit_asks_then_retries() -> None:
    limited = httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow"}})
    outcome, requests, pauses = await run([limited, httpx.Response(200, json=body("ok"))])
    assert getattr(outcome, "content", None) == "ok"
    assert (len(requests), pauses) == (2, [7.0])


async def test_gives_up_after_repeated_rate_limits() -> None:
    limited = [httpx.Response(429, json={"error": {"message": "slow"}}) for _ in range(3)]
    outcome, requests, pauses = await run(limited)
    assert isinstance(outcome, RateLimitedError)
    assert (len(requests), pauses) == (3, [1.0, 2.0])


async def test_long_waits_are_capped() -> None:
    limited = httpx.Response(429, headers={"retry-after": "3600"})
    _, _, pauses = await run([limited, httpx.Response(200, json=body())])
    assert pauses == [30.0]


async def test_server_errors_are_retried() -> None:
    outcome, _, _ = await run([httpx.Response(503), httpx.Response(200, json=body("ok"))])
    assert getattr(outcome, "content", None) == "ok"


async def test_garbled_tool_calls_and_other_errors_are_typed() -> None:
    garbled = httpx.Response(400, json={"error": {"message": "x", "code": "tool_use_failed"}})
    outcome, _, _ = await run([garbled])
    assert isinstance(outcome, ToolUseFailedError)
    outcome, requests, _ = await run([httpx.Response(401, json={"error": {"message": "bad key"}})])
    assert isinstance(outcome, LLMError)
    assert not isinstance(outcome, RateLimitedError)
    assert outcome.status == 401
    assert len(requests) == 1


@pytest.mark.parametrize("text", ["", "not json"])
async def test_errors_without_a_json_body(text: str) -> None:
    outcome, _, _ = await run([httpx.Response(400, text=text)])
    assert isinstance(outcome, LLMError)
