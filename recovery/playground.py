"""Agent Playground: run one synthetic failed payment through the LIVE pipeline.

The page at ``/playground`` is a visual layer only. Every decision it shows
comes from ``POST /playground/api/run``, which does NOT re-implement recovery:

    validated demo input -> synthetic payment in an in-memory DemoProvider
    -> InternalEvent (provider="demo") -> RecoveryPipeline.process
       (the same PolicyRouter, agent loop, GuardedExecutor, trajectory store
        and ledger the webhook path uses)
    -> read back the ledger row, trajectory and executor audit it produced

Safety properties, each covered by tests:

* The demo provider is registered on the pipeline only, never on the webhook
  router, so ``/webhooks/demo`` does not exist and webhook handling is
  unchanged. It is an in-memory fake: no network, no money moves.
* Demo payment and customer ids must carry a ``demo`` namespace
  (``pay_demo_...`` / ``cust_demo_...``). Guard state (idempotency, per-subject
  budgets) is keyed on these ids, so a demo run can never consume the budget
  or idempotency keys of a real provider payment or customer.
* The client supplies the *payment* being simulated, never an action or a
  refund amount. Refunds still derive their amount server-side from the
  (demo) payment record, and every effect goes through the guarded executor,
  approval gate included. There is no approve route.
* Responses are marked ``simulation`` and never claim a recovery: the ledger's
  ``amount_recovered_inr`` stays null because nobody pays in a simulation.
* A small process-wide rate limit bounds how often an anonymous visitor can
  drive the pipeline (and, with ``LLM_BACKEND=groq``, a paid model).
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from agentcore.distill import DemotionPolicy, PromotionPolicy
from providers.fake import FakeProvider
from providers.types import EventType, FailureReason, Payment, PaymentMethod, PaymentStatus
from recovery import distiller
from recovery.dashboard import _live_rules, _precondition, load_snapshot
from recovery.db import session_scope
from recovery.ledger import LedgerRepository
from recovery.models import InternalEvent, LedgerEntry
from recovery.trajectory_store import TrajectoryStore

router = APIRouter()

DEMO_PROVIDER = "demo"
DISCLAIMER = "No real customer money was moved or recovered by this demo."
_PAGE = Path(__file__).resolve().parent / "playground.html"
_THOUGHT_CHARS = 240


class DemoProvider(FakeProvider):
    """In-memory sandbox provider for playground runs. Never takes webhooks."""

    name = DEMO_PROVIDER

    def __init__(self) -> None:
        # A random secret nobody knows: the adapter is not on the webhook
        # router anyway, but it should not share the fake provider's secret.
        super().__init__(webhook_secret=secrets.token_hex(16))


class RateLimiter:
    """Sliding-window limit on playground runs for the whole process."""

    def __init__(self, max_runs: int = 20, window_seconds: float = 60.0) -> None:
        self._max = max_runs
        self._window = window_seconds
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > self._window:
                self._hits.popleft()
            if len(self._hits) >= self._max:
                return False
            self._hits.append(now)
            return True


class ScenarioIn(BaseModel):
    """A synthetic failed payment. Describes the payment, never the decision.

    Unknown fields are rejected, so a client cannot smuggle in an action, a
    route or a refund amount.
    """

    model_config = ConfigDict(extra="forbid")

    payment_id: str = Field(pattern=r"^pay_demo_[A-Za-z0-9]{1,32}$")
    amount_inr: float = Field(gt=0, le=1_000_000)
    failure_reason: FailureReason
    customer_id: str | None = Field(default=None, pattern=r"^cust_demo_[A-Za-z0-9]{1,32}$")


def _seed_demo_payment(provider: DemoProvider, scenario: ScenarioIn) -> None:
    provider.add_payment(
        Payment(
            id=scenario.payment_id,
            amount_inr=scenario.amount_inr,
            currency="INR",
            status=PaymentStatus.FAILED,
            method=PaymentMethod.CARD,
            customer_id=scenario.customer_id,
            failure_reason=scenario.failure_reason,
            created_at=datetime.now(timezone.utc),
        )
    )


def _persist_demo_event(scenario: ScenarioIn) -> tuple[int, str]:
    event_id = f"demo_evt_{secrets.token_hex(8)}"
    with session_scope() as session:
        row = InternalEvent(
            provider=DEMO_PROVIDER,
            event_id=event_id,
            event_type=EventType.PAYMENT_FAILED.value,
            payment_id=scenario.payment_id,
            amount_inr=scenario.amount_inr,
            failure_reason=scenario.failure_reason.value,
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(row)
        session.flush()
        return row.id, event_id


def _trajectory_view(trajectory_id: int | None) -> dict[str, Any] | None:
    if trajectory_id is None:
        return None
    trajectory = TrajectoryStore().load(trajectory_id)
    if trajectory is None:
        return None
    return {
        "id": trajectory_id,
        "outcome": trajectory.outcome.value,
        "resolution_action": trajectory.resolution_action,
        "prompt_id": trajectory.system_prompt_id,
        "steps": [
            {
                "index": s.index,
                "tool": s.tool,
                "decision": s.decision,
                "thought": (s.thought or "")[:_THOUGHT_CHARS],
            }
            for s in trajectory.steps
        ],
    }


def build_result(ledger_id: int, audit: list[Any], llm_model: str) -> dict[str, Any]:
    """Assemble the response purely from what the pipeline persisted."""
    row = LedgerRepository().get(ledger_id)
    assert row is not None
    trajectory = _trajectory_view(row.trajectory_id)
    llm_calls = len(trajectory["steps"]) if trajectory else 0
    return {
        "mode": "simulation",
        "provider": row.provider,
        "money_moved": False,
        "disclaimer": DISCLAIMER,
        "ledger_id": row.id,
        "event": {
            "internal_event_id": row.internal_event_id,
            "event_id": row.event_id,
            "payment_id": row.payment_id,
            "amount_inr": row.amount_inr,
            "failure_reason": row.failure_reason,
        },
        "path": row.path,
        "rule": (
            {"key": row.rule_key, "version": row.rule_version} if row.rule_key else None
        ),
        "action": row.action,
        "llm": {
            "calls": llm_calls,
            "model": llm_model if row.path == "llm" else None,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "modelled_cost_usd": row.modelled_cost_usd,
        },
        "executor": {
            "status": row.execution_status,
            "approval_id": row.approval_id,
            "audit": [
                {"action": a.action_name, "decision": a.decision, "detail": a.detail}
                for a in audit
            ],
        },
        "outcome": row.outcome,
        "amount_recovered_inr": row.amount_recovered_inr,
        "error": row.error,
        "trajectory": trajectory,
        "latency_ms": round(row.latency_ms, 2),
    }


@router.get("/playground", response_class=HTMLResponse)
def playground_page() -> HTMLResponse:
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


@router.get("/playground/api/overview")
def overview(request: Request) -> dict[str, Any]:
    """Live system state for the page, plus the offline evaluation headline."""
    state = request.app.state
    settings = state.settings
    pipeline = getattr(state, "pipeline", None)
    rules = [
        {
            "rule_key": r["rule_key"],
            "version": r["version"],
            "status": r["status"],
            "priority": r["priority"],
            "precondition": _precondition(r["definition"] or {}),
            "action": ((r["definition"] or {}).get("action") or {}).get("name"),
            "origin": (r["provenance"] or {}).get("origin"),
        }
        for r in _live_rules()
    ]
    promotion, demotion = PromotionPolicy(), DemotionPolicy()
    snapshot = load_snapshot(state.artifacts_dir)
    evaluation = None
    if snapshot is not None:
        metrics = snapshot["metrics"]
        evaluation = {
            "label": "Offline evaluation · synthetic cases · modelled cost",
            "n": snapshot["n"],
            "seed": snapshot["seed"],
            "drift_index": snapshot["drift_index"],
            "arms": {
                arm: {
                    "llm_share": metrics[arm]["llm_share"]["rate"],
                    "recovery": metrics[arm]["raw_recovery"]["rate"],
                    "recovery_ci": [
                        metrics[arm]["raw_recovery"]["ci_low"],
                        metrics[arm]["raw_recovery"]["ci_high"],
                    ],
                    "cost_per_1000_usd": metrics[arm]["modelled_cost_per_1000_usd"],
                }
                for arm in ("baseline", "distilled")
            },
            "promoted": snapshot["distillation"].get("promoted", []),
            "demoted": snapshot["distillation"].get("demoted", []),
            "rules": [
                {
                    "rule_key": r["rule_key"],
                    "status": r["status"],
                    "precondition": _precondition(r.get("definition") or {}),
                    "action": ((r.get("definition") or {}).get("action") or {}).get("name"),
                }
                for r in snapshot.get("rules", [])
            ],
        }
    return {
        "system": {"online": pipeline is not None, "mode": "simulation"},
        "webhooks": {"providers": sorted(state.provider_registry)},
        "llm": {"backend": settings.llm_backend, "model": state.playground_llm_model},
        "guardrails": {
            "active": getattr(state, "guard_store", None) is not None,
            "approval_threshold_inr": settings.approval_threshold_inr,
            "per_subject_action_budget": settings.per_subject_action_budget,
            "per_run_spend_cap_inr": settings.per_run_spend_cap_inr,
        },
        "rules": rules,
        "lifecycle": {
            "cadence": distiller.DEFAULT_CADENCE,
            "min_cluster_support": distiller.MIN_CLUSTER_SUPPORT,
            "min_cluster_agreement": distiller.MIN_CLUSTER_AGREEMENT,
            "promote_min_support": promotion.min_support,
            "promote_min_agreement": promotion.min_agreement,
            "promote_min_agreement_lb": promotion.min_agreement_lb,
            "promote_min_recovery_lb": promotion.min_recovery_lb,
            "demote_window": demotion.min_recent,
            "demote_recovery_ub_floor": demotion.recovery_ub_floor,
            "live_auto_distillation": False,
        },
        "evaluation": evaluation,
        "failure_reasons": [reason.value for reason in FailureReason],
        "disclaimer": DISCLAIMER,
    }


@router.get("/playground/api/activity")
def activity(limit: int = 8) -> dict[str, Any]:
    """The most recent playground runs, exactly as the ledger recorded them."""
    limit = max(1, min(limit, 25))
    with session_scope() as session:
        rows = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.provider == DEMO_PROVIDER)
            .order_by(LedgerEntry.id.desc())
            .limit(limit)
            .all()
        )
        runs = [
            {
                "ledger_id": row.id,
                "recorded_at": (
                    row.created_at.replace(tzinfo=timezone.utc)
                    if row.created_at.tzinfo is None
                    else row.created_at
                ).isoformat(),
                "failure_reason": row.failure_reason,
                "path": row.path,
                "llm_calls": row.tool_calls,
                "action": row.action,
                "execution_status": row.execution_status,
                "outcome": row.outcome,
            }
            for row in rows
        ]
    return {"mode": "simulation", "runs": runs}


@router.post("/playground/api/run")
def run_scenario(scenario: ScenarioIn, request: Request) -> dict[str, Any]:
    """Run one synthetic failed payment through the live recovery pipeline."""
    state = request.app.state
    pipeline = getattr(state, "pipeline", None)
    if pipeline is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "pipeline not started")
    if not state.playground_limiter.allow():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "playground rate limit; retry shortly")

    _seed_demo_payment(state.demo_provider, scenario)
    internal_id, _ = _persist_demo_event(scenario)

    guard_store = state.guard_store
    audit_before = len(guard_store.read_audit(scenario.payment_id))
    ledger_id = pipeline.process(internal_id)
    if ledger_id is None:  # cannot happen for a payment_failed event; fail loudly
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "event was not processed")
    audit = guard_store.read_audit(scenario.payment_id)[audit_before:]
    return build_result(ledger_id, audit, state.playground_llm_model)
