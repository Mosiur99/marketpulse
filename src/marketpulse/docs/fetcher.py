"""Downloads a document only when it changed since the last fetch (HTTP ETag)."""

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class DocSource:
    raw_url: str
    page_url: str


@dataclass(frozen=True)
class Fetched:
    text: str
    etag: str | None


async def fetch(client: httpx.AsyncClient, url: str, etag: str | None) -> Fetched | None:
    """Returns None when the server says the document has not changed."""
    headers = {"If-None-Match": etag} if etag else {}
    response = await client.get(url, headers=headers)
    if response.status_code == httpx.codes.NOT_MODIFIED:
        return None
    response.raise_for_status()
    return Fetched(response.text, response.headers.get("etag"))
