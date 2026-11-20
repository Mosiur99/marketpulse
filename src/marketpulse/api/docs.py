from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from marketpulse.docs.embedder import Embedder, FastEmbedEmbedder
from marketpulse.docs.search import DocSearch, Mode

router = APIRouter(tags=["docs"])


class DocResult(BaseModel):
    heading_path: str
    url: str
    content: str
    score: float


class DocResults(BaseModel):
    query: str
    mode: Mode
    results: list[DocResult]


def embedder(request: Request) -> Embedder:
    state = request.app.state
    if state.embedder is None:
        settings = state.settings
        state.embedder = FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
    found: Embedder = state.embedder
    return found


@router.get("/docs/search", response_model=DocResults)
async def search_docs(
    request: Request,
    q: Annotated[str, Query(min_length=2, max_length=300, description="Question or keywords")],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    mode: Annotated[Mode, Query(description="hybrid, vector or keyword")] = "hybrid",
) -> DocResults:
    hits = await DocSearch(request.app.state.engine, embedder(request)).search(q, limit, mode)
    return DocResults(
        query=q,
        mode=mode,
        results=[
            DocResult(heading_path=h.heading_path, url=h.url, content=h.content, score=h.score)
            for h in hits
        ],
    )
