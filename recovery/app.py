"""FastAPI application factory.

Constructing the app calls :func:`config.get_settings`, which triggers the
sandbox-key assertion — so the app refuses to boot with live provider keys.
Database tables are created on startup via the lifespan handler, so merely
importing this module does not touch the filesystem.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import get_settings
from providers.base import PaymentProvider
from recovery import webhooks
from recovery.db import init_db
from recovery.providers_registry import build_registry


def create_app(
    *,
    provider_registry: dict[str, PaymentProvider] | None = None,
    database_url: str | None = None,
) -> FastAPI:
    """Build and return the FastAPI app.

    ``provider_registry`` and ``database_url`` may be injected for tests; by
    default they are built from settings.
    """
    settings = get_settings()  # sandbox-key assertion runs here
    registry = provider_registry or build_registry(settings)
    db_url = database_url or settings.database_url

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db(db_url)
        yield

    app = FastAPI(
        title="Self-Distilling Payment Recovery Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.provider_registry = registry
    app.include_router(webhooks.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()
