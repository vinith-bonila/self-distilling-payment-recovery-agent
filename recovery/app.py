"""FastAPI application factory.

Constructing the app validates settings, which triggers the sandbox-key
assertion — so the app refuses to boot with live provider keys (and refuses
``LLM_BACKEND=groq`` without a key). Everything that touches storage happens in
the lifespan handler, so merely importing this module does not touch the
filesystem.

At startup the lifespan:

1. creates the tables,
2. opens the persistent guard store (idempotency, approvals, budgets, spend
   caps and the audit log survive restarts and are shared by every request),
3. inserts the seed rules if — and only if — the rule store is empty,
4. builds the live :class:`~recovery.pipeline.RecoveryPipeline`, and
5. drains any failed-payment events a previous process persisted but did not
   get to process.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from agentcore.guardrails import SqlGuardStore
from agentcore.llm_client import LLMClient
from config import Settings, get_settings
from providers.base import PaymentProvider
from recovery import dashboard, webhooks
from recovery.db import init_db
from recovery.llm_factory import build_llm
from recovery.pipeline import RecoveryPipeline
from recovery.providers_registry import build_registry
from recovery.rule_schema import payment_rule_schema
from recovery.rules_repo import RuleRepository, RuleStatus
from recovery.seed_rules import seed


def _rule_store_is_empty(repo: RuleRepository) -> bool:
    return not any(repo.list_by_status(status) for status in RuleStatus)


def create_app(
    *,
    settings: Settings | None = None,
    provider_registry: dict[str, PaymentProvider] | None = None,
    database_url: str | None = None,
    artifacts_dir: Path | None = None,
    llm: LLMClient | None = None,
) -> FastAPI:
    """Build and return the FastAPI app.

    Every argument may be injected for tests; by default they come from
    settings, the provider registry, the repo's ``evals/`` dir and the
    configured LLM backend (the offline stub unless ``LLM_BACKEND=groq``).
    """
    settings = settings or get_settings()  # sandbox-key assertion runs here
    registry = provider_registry or build_registry(settings)
    db_url = database_url or settings.database_url
    llm_client = llm or build_llm(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(db_url)
        guard_store = SqlGuardStore(db_url)
        if settings.seed_rules_on_startup:
            repo = RuleRepository(payment_rule_schema())
            if _rule_store_is_empty(repo):
                seed(repo)
        pipeline = RecoveryPipeline(
            registry=registry, settings=settings, llm=llm_client, guard_store=guard_store
        )
        app.state.guard_store = guard_store
        app.state.pipeline = pipeline
        if settings.process_pending_on_startup:
            pipeline.process_pending()
        yield

    app = FastAPI(
        title="Self-Distilling Payment Recovery Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.provider_registry = registry
    app.state.artifacts_dir = artifacts_dir or dashboard.DEFAULT_ARTIFACTS_DIR
    app.include_router(webhooks.router)
    app.include_router(dashboard.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()
