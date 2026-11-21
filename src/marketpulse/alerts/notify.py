"""Delivery: Telegram when a bot token is set, otherwise the log."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4_096  # Telegram's limit for one message
MAX_WAIT_SECONDS = 60.0


class DeliveryError(Exception):
    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


class Notifier(Protocol):
    name: str

    async def send(self, text: str) -> int:
        """Delivers the text and returns the number of attempts it took."""
        ...


class LogNotifier:
    """Used when no channel is configured, so alerts still run and show up in the log."""

    name = "log"

    async def send(self, text: str) -> int:
        log.info("notification: %s", text)
        return 1


class TelegramNotifier:
    name = "telegram"

    def __init__(
        self,
        http: httpx.AsyncClient,
        token: str,
        chat_id: str,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        base_url: str = TELEGRAM_API,
    ) -> None:
        self._http = http
        self._url = f"{base_url}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._max_attempts = max_attempts
        self._sleep = sleep

    async def send(self, text: str) -> int:
        payload = {
            "chat_id": self._chat_id,
            "text": text[:MAX_MESSAGE_CHARS],
            "disable_web_page_preview": True,
        }
        error = "not sent"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._http.post(self._url, json=payload)
            except httpx.TransportError as exc:
                error, wait = f"network error: {type(exc).__name__}", 2.0**attempt
            else:
                if response.is_success:
                    return attempt
                error = f"Telegram {response.status_code}: {description(response)}"
                if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                    wait = retry_after(response, attempt)
                elif response.status_code >= 500:
                    wait = 2.0**attempt
                else:
                    # A wrong token or chat id will not fix itself.
                    raise DeliveryError(error, attempt)
            if attempt < self._max_attempts:
                await self._sleep(wait)
        raise DeliveryError(error, self._max_attempts)


def description(response: httpx.Response) -> str:
    try:
        return str(response.json().get("description", ""))[:200]
    except ValueError:
        return response.text[:200]


def retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        seconds = float(response.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        seconds = 2.0**attempt
    return min(max(seconds, 0.0), MAX_WAIT_SECONDS)
