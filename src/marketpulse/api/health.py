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
    return Health(
        status="ok" if database_up else "degraded", database="up" if database_up else "down"
    )
