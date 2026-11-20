from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from marketpulse.db import is_database_up

router = APIRouter(tags=["health"])


class Ingest(BaseModel):
    connected: bool
    lag_seconds: float | None
    trades_saved: int


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["up", "down"]
    ingest: Ingest | None = None


@router.get("/health", response_model=Health)
async def health(request: Request, response: Response) -> Health:
    database_up = await is_database_up(request.app.state.engine)
    if not database_up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    ingestor = request.app.state.ingestor
    ingest = (
        None
        if ingestor is None
        else Ingest(
            connected=ingestor.connected,
            lag_seconds=ingestor.lag_seconds(),
            trades_saved=ingestor.trades_saved,
        )
    )
    return Health(
        status="ok" if database_up else "degraded",
        database="up" if database_up else "down",
        ingest=ingest,
    )
