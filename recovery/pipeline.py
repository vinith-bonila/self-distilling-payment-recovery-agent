"""The live recovery pipeline.

Runs after a webhook has been verified, persisted and normalised (and after the
HTTP 200 has been sent):

    InternalEvent -> provider enrichment -> PolicyRouter
        ACTIVE rule matches -> the rule's action          (no LLM call)
        otherwise           -> the agent loop + its tools (LLM)
    -> GuardedExecutor (the only path to a provider side effect)
    -> trajectory (agent path) -> outcome ledger

Everything here is assembled from existing components: the router and rule
store, ``build_executor``/``run_recovery`` (the same executor, tools and
guardrail policy the evaluation uses), the trajectory store, and the provider
registry. Nothing re-implements a guardrail.

Guard state is persistent (``SqlGuardStore``), so idempotency keys, approvals,
budgets and spend caps hold across requests and restarts. A redelivered
webhook (same provider + event id) is detected before routing, so it causes no
second LLM call and no second action; the executor's idempotency keys are the
second line of defence for distinct events about the same payment.

Processing is serialised by a process-wide lock. That makes the check-then-act
steps (ledger, idempotency, spend cap) safe within one process; it does not make
them safe across several worker processes — see the README.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from agentcore.agent import ToolArgError, validate_args
from agentcore.guardrails import ExecutionStatus, GuardStore
from agentcore.llm_client import LLMClient
from config import Settings
from providers.base import PaymentProvider
from providers.errors import ProviderError, ResourceNotFound
from providers.types import EventType, FailureReason, NormalisedEvent
from recovery.agent import build_executor, run_recovery
from recovery.context import build_context
from recovery.db import session_scope
from recovery.ledger import LedgerRepository
from recovery.models import InternalEvent, LedgerEntry
from recovery.router import PolicyRouter, Route
from recovery.rule_schema import payment_rule_schema
from recovery.rules_repo import RuleRepository
from recovery.tools import RecoveryCase
from recovery.trajectory_store import TrajectoryStore

logger = logging.getLogger(__name__)

# A rule's action is carried out by the SAME tool the agent would call, so the
# deterministic path reuses the tool's Action builder and the guarded executor.
_RULE_ACTION_TO_TOOL = {
    "send_payment_link": "create_payment_link",
    "refund": "refund_payment",
    "escalate_to_human": "escalate_to_human",
}

_REJECTED = {
    ExecutionStatus.REJECTED_ACTION_BUDGET,
    ExecutionStatus.REJECTED_SPEND_CAP,
    ExecutionStatus.REJECTED_BREAKER_OPEN,
}


def _outcome_for(status: ExecutionStatus, action: str) -> str:
    """Map an executor status to the ledger's outcome vocabulary.

    Uses the same words as the agent loop's ``AgentOutcome`` so rule and agent
    rows are comparable.
    """
    if status is ExecutionStatus.PENDING_APPROVAL:
        return "pending_approval"
    if status is ExecutionStatus.DUPLICATE:
        return "duplicate"
    if status in _REJECTED:
        return "rejected"
    return "escalated" if action == "escalate_to_human" else "resolved"


def _normalised(row: InternalEvent) -> NormalisedEvent:
    return NormalisedEvent(
        provider=row.provider,
        event_id=row.event_id,
        event_type=EventType(row.event_type),
        payment_id=row.payment_id,
        amount_inr=row.amount_inr,
        failure_reason=FailureReason(row.failure_reason) if row.failure_reason else None,
        occurred_at=datetime.fromisoformat(row.occurred_at) if row.occurred_at else None,
    )


class RecoveryPipeline:
    """Processes normalised failed-payment events end to end."""

    def __init__(
        self,
        *,
        registry: dict[str, PaymentProvider],
        settings: Settings,
        llm: LLMClient,
        guard_store: GuardStore,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._llm = llm
        self._store = guard_store
        self._router = PolicyRouter(RuleRepository(payment_rule_schema()))
        self._ledger = LedgerRepository()
        self._trajectories = TrajectoryStore()
        self._lock = threading.Lock()

    # --- entry points -----------------------------------------------------

    def process(self, internal_event_id: int) -> int | None:
        """Process one normalised event; return its ledger row id.

        Returns ``None`` for events that are not payment failures. Never raises:
        a failure is recorded in the ledger with ``outcome='failed'``.
        """
        with self._lock:
            return self._process_locked(internal_event_id)

    def process_pending(self, limit: int = 500) -> int:
        """Process failed-payment events that have no ledger row yet.

        This is the crash-recovery path: an event persisted by the webhook but
        not processed (e.g. the process died after returning 200) is picked up
        here, typically at startup.
        """
        with session_scope() as session:
            ids = [
                row_id
                for (row_id,) in session.query(InternalEvent.id)
                .outerjoin(LedgerEntry, LedgerEntry.internal_event_id == InternalEvent.id)
                .filter(LedgerEntry.id.is_(None))
                .filter(InternalEvent.event_type == EventType.PAYMENT_FAILED.value)
                .order_by(InternalEvent.id)
                .limit(limit)
                .all()
            ]
        for row_id in ids:
            self.process(row_id)
        return len(ids)

    # --- core -------------------------------------------------------------

    def _process_locked(self, internal_event_id: int) -> int | None:
        existing = self._ledger.for_event(internal_event_id)
        if existing is not None:
            return existing.id  # already processed

        with session_scope() as session:
            row = session.get(InternalEvent, internal_event_id)
            if row is None:
                return None
            event = _normalised(row)
        if event.event_type is not EventType.PAYMENT_FAILED:
            return None

        base = {
            "internal_event_id": internal_event_id,
            "provider": event.provider,
            "event_id": event.event_id,
            "payment_id": event.payment_id,
            "amount_inr": event.amount_inr,
            "failure_reason": event.failure_reason.value if event.failure_reason else None,
        }

        # Redelivery of an event we already acted on: record it, do nothing else.
        earlier = self._ledger.processed_delivery(event.provider, event.event_id)
        if earlier is not None:
            return self._ledger.record(
                **base,
                path="duplicate_delivery",
                outcome="duplicate",
                error=f"duplicate of ledger row {earlier.id}; not re-processed",
            )

        started = time.monotonic()
        try:
            fields = self._route_and_act(event)
        except Exception as exc:  # noqa: BLE001 — record, never crash the worker
            logger.exception("recovery failed for internal event %s", internal_event_id)
            fields = {"path": "failed", "outcome": "failed", "error": repr(exc)[:2000]}
        fields["latency_ms"] = (time.monotonic() - started) * 1000.0

        ledger_id = self._ledger.record(**base, **fields)
        self._mark_event(internal_event_id, fields)
        return ledger_id

    def _route_and_act(self, event: NormalisedEvent) -> dict[str, Any]:
        provider = self._registry.get(event.provider)
        if provider is None:
            raise ProviderError(f"no adapter registered for provider {event.provider!r}")

        # Enrich from the authoritative provider record (normalised types only).
        payment = provider.get_payment(event.payment_id)
        history = None
        if payment.customer_id:
            try:
                history = provider.get_customer_history(payment.customer_id)
            except ResourceNotFound:
                history = None
        hours = None
        if payment.created_at is not None:
            elapsed = datetime.now(timezone.utc) - payment.created_at
            hours = max(0.0, elapsed.total_seconds() / 3600.0)

        context = build_context(
            event, method=payment.method, history=history, hours_since_last_attempt=hours
        )
        reason = (event.failure_reason or FailureReason.UNKNOWN).value
        case = RecoveryCase(
            payment_id=payment.id,
            amount_inr=payment.amount_inr,
            failure_reason=reason,
            method=payment.method.value,
            customer_id=payment.customer_id,
            order_id=payment.order_id,
        )

        decision = self._router.route(context)
        if decision.route is Route.DETERMINISTIC:
            return self._run_rule(case, provider, decision)
        return self._run_agent(case, provider)

    def _run_rule(self, case: RecoveryCase, provider: PaymentProvider, decision) -> dict[str, Any]:
        """Carry out an ACTIVE rule's action through the guarded executor. No LLM."""
        fields: dict[str, Any] = {
            "path": "deterministic",
            "action": decision.action,
            "rule_key": decision.rule_key,
            "rule_version": decision.rule_version,
        }
        tool_name = _RULE_ACTION_TO_TOOL.get(decision.action or "")
        if tool_name is None:
            # e.g. wait_and_retry / no_action: nothing to execute now.
            fields.update(outcome="no_effect")
            return fields

        executor, registry = build_executor(case, provider, self._settings, store=self._store)
        spec = registry.get(tool_name)
        assert spec is not None and spec.build_action is not None
        # A link is for the authoritative payment amount; refunds derive their
        # own amount inside the tool from the provider's payment record.
        args = {"amount_inr": case.amount_inr} if "amount_inr" in spec.params else {}
        try:
            action = spec.build_action(validate_args(spec, args))
        except ToolArgError as exc:
            fields.update(outcome="failed", error=f"rule arguments invalid: {exc}")
            return fields

        result = executor.execute(action)
        fields.update(
            execution_status=result.status.value,
            outcome=_outcome_for(result.status, action.name),
            approval_id=result.approval_id,
        )
        return fields

    def _run_agent(self, case: RecoveryCase, provider: PaymentProvider) -> dict[str, Any]:
        """Run the existing agent loop; its effects go through the executor."""
        trajectory = run_recovery(case, provider, self._llm, self._settings, store=self._store)
        trajectory_id = self._trajectories.save(trajectory)

        execution_status = None
        for step in reversed(trajectory.steps):
            if step.decision.startswith("effect_"):
                execution_status = step.decision[len("effect_"):]
                break

        approval_id = None
        if trajectory.outcome.value == "pending_approval":
            pending = self._store.list_pending(case.payment_id)
            if pending:
                approval_id = max(pending, key=lambda r: r.created_at).approval_id

        return {
            "path": "llm",
            "action": trajectory.resolution_action,
            "execution_status": execution_status,
            "outcome": trajectory.outcome.value,
            "approval_id": approval_id,
            "trajectory_id": trajectory_id,
            "tool_calls": trajectory.iterations,
            "prompt_tokens": trajectory.total_prompt_tokens,
            "completion_tokens": trajectory.total_completion_tokens,
            "modelled_cost_usd": (
                trajectory.total_prompt_tokens / 1000.0 * self._settings.modelled_input_usd_per_1k
                + trajectory.total_completion_tokens / 1000.0 * self._settings.modelled_output_usd_per_1k
            ),
        }

    def _mark_event(self, internal_event_id: int, fields: dict[str, Any]) -> None:
        route = {
            "deterministic": Route.DETERMINISTIC.value,
            "llm": Route.ESCALATE_TO_AGENT.value,
        }.get(fields.get("path", ""))
        with session_scope() as session:
            row = session.get(InternalEvent, internal_event_id)
            if row is None:
                return
            row.route = route
            row.matched_rule_key = fields.get("rule_key")
            row.matched_rule_version = fields.get("rule_version")
            row.resolved_action = fields.get("action")
