from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from marketpulse.api import health
from marketpulse.config import Settings, get_settings
from marketpulse.db import create_engine


def create_app(settings: Settings | None = None, engine: AsyncEngine | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = engine or create_engine(settings)
        try:
            yield
        finally:
            if owned:
                await app.state.engine.dispose()

    app = FastAPI(title="MarketPulse", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings), host="0.0.0.0", port=8000, log_level=settings.log_level.lower()
    )
