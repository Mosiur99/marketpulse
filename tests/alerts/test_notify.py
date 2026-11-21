import json

import httpx
import pytest

from marketpulse.alerts.notify import (
    MAX_MESSAGE_CHARS,
    DeliveryError,
    LogNotifier,
    TelegramNotifier,
)

Reply = httpx.Response | Exception


def telegram(replies: list[Reply]) -> tuple[TelegramNotifier, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    slept: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return TelegramNotifier(http, "123:abc", "42", sleep=sleep), requests, slept


async def test_sends_the_text_to_the_chat() -> None:
    notifier, requests, _ = telegram([httpx.Response(200, json={"ok": True})])
    assert await notifier.send("BTC fell 3%") == 1
    assert str(requests[0].url) == "https://api.telegram.org/bot123:abc/sendMessage"
    assert json.loads(requests[0].content) == {
        "chat_id": "42",
        "text": "BTC fell 3%",
        "disable_web_page_preview": True,
    }


async def test_waits_as_long_as_telegram_asks_after_a_rate_limit() -> None:
    limited = httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}})
    notifier, _, slept = telegram([limited, httpx.Response(200, json={"ok": True})])
    assert await notifier.send("x") == 2
    assert slept == [7.0]


async def test_retries_server_and_network_errors_with_backoff() -> None:
    replies: list[Reply] = [
        httpx.Response(502, text="bad gateway"),
        httpx.ConnectError("down"),
        httpx.Response(200, json={"ok": True}),
    ]
    notifier, _, slept = telegram(replies)
    assert await notifier.send("x") == 3
    assert slept == [2.0, 4.0]


async def test_gives_up_after_the_last_attempt() -> None:
    notifier, requests, _ = telegram([httpx.Response(500, json={"description": "oops"})] * 3)
    with pytest.raises(DeliveryError, match="Telegram 500: oops") as caught:
        await notifier.send("x")
    assert (caught.value.attempts, len(requests)) == (3, 3)


async def test_a_wrong_chat_or_token_is_not_retried() -> None:
    bad = httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
    notifier, requests, slept = telegram([bad])
    with pytest.raises(DeliveryError, match="chat not found") as caught:
        await notifier.send("x")
    assert (caught.value.attempts, len(requests), slept) == (1, 1, [])


async def test_long_text_is_cut_to_telegrams_limit() -> None:
    notifier, requests, _ = telegram([httpx.Response(200, json={"ok": True})])
    await notifier.send("a" * (MAX_MESSAGE_CHARS + 50))
    assert len(json.loads(requests[0].content)["text"]) == MAX_MESSAGE_CHARS


async def test_errors_never_include_the_token() -> None:
    notifier, _, _ = telegram([httpx.ConnectError("https://api.telegram.org/bot123:abc")] * 3)
    with pytest.raises(DeliveryError) as caught:
        await notifier.send("x")
    assert "123:abc" not in str(caught.value)


async def test_the_log_notifier_always_delivers(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    assert await LogNotifier().send("hello") == 1
    assert "hello" in caplog.text


def test_request_urls_with_the_bot_token_are_not_logged() -> None:
    import logging

    from marketpulse.main import configure_logging

    configure_logging("INFO")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
