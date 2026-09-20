"""FastAPI application factory.

Constructing the app calls :func:`config.get_settings`, which triggers the
sandbox-key assertion — so the app refuses to boot with live provider keys.
Database tables are created on startup via the lifespan handler, so merely
importing this module does not touch the filesystem.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from config import get_settings
from recovery.db import init_db


def create_app() -> FastAPI:
    """Build and return the FastAPI app."""
    settings = get_settings()  # sandbox-key assertion runs here

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db(settings.database_url)
        yield

    app = FastAPI(
        title="Self-Distilling Payment Recovery Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()
