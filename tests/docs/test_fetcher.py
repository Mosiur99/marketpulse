import httpx
import pytest

from marketpulse.docs.fetcher import fetch

URL = "https://docs.example/rest-api.md"


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_first_fetch_returns_the_text_and_etag() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="# Doc", headers={"ETag": '"v1"'})

    async with client_for(httpx.MockTransport(handler)) as client:
        fetched = await fetch(client, URL, None)
    assert fetched is not None
    assert (fetched.text, fetched.etag) == ("# Doc", '"v1"')
    assert "if-none-match" not in seen[0].headers


async def test_unchanged_document_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    async with client_for(httpx.MockTransport(handler)) as client:
        assert await fetch(client, URL, '"v1"') is None


async def test_server_errors_are_raised() -> None:
    async with client_for(httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch(client, URL, None)
