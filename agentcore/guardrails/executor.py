"""The guarded executor: the only path to a side effect.

An effect happens only when a caller hands an :class:`Action` (pure data) to
:meth:`GuardedExecutor.execute`. The executor resolves a privately registered
handler and runs it exactly once, and only after every guard has passed. There
is no public method that runs a handler outside this path.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from agentcore.guardrails.errors import ApprovalError, UnknownActionError
from agentcore.guardrails.model import (
    Action,
    ApprovalState,
    ExecutionResult,
    ExecutionStatus,
    GuardPolicy,
)
from agentcore.guardrails.store import GuardStore

EffectHandler = Callable[[Action], Any]
"""A handler performs the real side effect for one action name and returns a
JSON-serialisable result. Handlers are held privately by the executor."""


class GuardedExecutor:
    """Runs actions through the full guardrail chain.

    The check order is deliberate:

    1. **Breaker** — if the run's spend-cap breaker is open, halt immediately.
    2. **Idempotency** — a key with a stored terminal result returns that
       result as a ``DUPLICATE``; the handler is never called again.
    3. **Approval** — cost strictly above the threshold routes to the pending
       queue and returns ``PENDING_APPROVAL`` without executing.
    4. **Action budget** — refuse once a subject has used its budget.
    5. **Spend cap** — if this cost would exceed the cap, trip the breaker and
       refuse (halting the run for all subsequent calls).
    6. **Execute** — run the handler exactly once, then record the spend, the
       budget consumption and the terminal idempotency result.

    Every call appends exactly one audit entry (an ``error`` entry if the
    handler raises).
    """

    def __init__(
        self,
        run_id: str,
        store: GuardStore,
        policy: GuardPolicy,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._run_id = run_id
        self._store = store
        self._policy = policy
        self._now = now or (lambda: datetime.now(timezone.utc))
        # Private registry: there is no accessor that returns a handler, so a
        # caller cannot fetch and invoke one outside the guarded path.
        self.__handlers: dict[str, EffectHandler] = {}

    @property
    def run_id(self) -> str:
        return self._run_id

    def register(self, action_name: str, handler: EffectHandler) -> None:
        """Register the effect handler for ``action_name`` (wiring-time only)."""
        self.__handlers[action_name] = handler

    def execute(self, action: Action) -> ExecutionResult:
        """Run ``action`` through the guardrail chain. See the class docstring."""
        # 1. Breaker halts the whole run.
        if self._store.is_breaker_open(self._run_id):
            return self._finish(
                action,
                ExecutionStatus.REJECTED_BREAKER_OPEN,
                detail="spend-cap breaker open; run halted",
            )

        # 2. Idempotency: a stored terminal result short-circuits with no effect.
        existing = self._store.get_result(self._run_id, action.idempotency_key)
        if existing is not None:
            return self._finish(
                action,
                ExecutionStatus.DUPLICATE,
                result=json.loads(existing.result_json),
                detail="idempotent replay of a completed action",
            )

        # No unguarded fallback: an action we cannot resolve is refused.
        if action.name not in self.__handlers:
            raise UnknownActionError(action.name)

        # 3. Approval gate. Strictly-above-threshold cost never auto-executes.
        if action.cost > self._policy.approval_threshold:
            approval = self._store.get_approval(self._run_id, action.idempotency_key)
            if approval is None:
                approval = self._store.create_pending(
                    self._run_id,
                    action.idempotency_key,
                    action.name,
                    action.subject_id,
                    action.cost,
                    json.dumps(dict(action.params)),
                )
            if approval.state is not ApprovalState.APPROVED:
                detail = (
                    "awaiting human approval"
                    if approval.state is ApprovalState.PENDING
                    else "approval was rejected; not executing"
                )
                return self._finish(
                    action,
                    ExecutionStatus.PENDING_APPROVAL,
                    approval_id=approval.approval_id,
                    detail=detail,
                )

        # 4. Per-subject action budget.
        used = self._store.subject_executed_count(self._run_id, action.subject_id)
        if used >= self._policy.per_subject_action_budget:
            return self._finish(
                action,
                ExecutionStatus.REJECTED_ACTION_BUDGET,
                detail=f"action budget exhausted for subject ({used} used)",
            )

        # 5. Per-run spend cap; the call that would exceed it trips the breaker.
        projected = self._store.accumulated_spend(self._run_id) + action.cost
        if projected > self._policy.per_run_spend_cap:
            self._store.trip_breaker(
                self._run_id,
                f"spend cap {self._policy.per_run_spend_cap} would be exceeded "
                f"({projected})",
            )
            return self._finish(
                action,
                ExecutionStatus.REJECTED_SPEND_CAP,
                detail="spend-cap breaker tripped by this action",
            )

        # 6. Execute exactly once, then record consumption and the result.
        handler = self.__handlers[action.name]
        try:
            result = handler(action)
        except Exception as exc:  # noqa: BLE001 — we re-raise after auditing
            self._audit(action, "error", detail=repr(exc))
            raise
        result_json = json.dumps(result)
        self._store.record_execution(self._run_id, action.subject_id, action.cost)
        self._store.put_result(
            self._run_id,
            action.idempotency_key,
            ExecutionStatus.EXECUTED.value,
            result_json,
        )
        return self._finish(
            action, ExecutionStatus.EXECUTED, result=result, detail="executed"
        )

    def approve(self, approval_id: str) -> ExecutionResult:
        """Approve a pending action and execute it through the guarded path.

        The action is reconstructed from the stored approval record, so a caller
        cannot approve one action and have a different (e.g. costlier) one run:
        the idempotency key, cost, subject and params are all fixed at
        enqueue time.
        """
        approval = self._require_approval(approval_id)
        if approval.state is ApprovalState.APPROVED:
            # Idempotent: re-approving returns the existing outcome.
            return self.execute(self._action_from_approval(approval))
        self._store.set_approval_state(approval_id, ApprovalState.APPROVED)
        return self.execute(self._action_from_approval(approval))

    def reject_approval(self, approval_id: str) -> None:
        """Deny a pending action. It will never execute under its key."""
        self._require_approval(approval_id)
        self._store.set_approval_state(approval_id, ApprovalState.REJECTED)

    def pending_approvals(self) -> list:
        """Return this run's pending-approval records."""
        return self._store.list_pending(self._run_id)

    # --- internals -------------------------------------------------------

    def _require_approval(self, approval_id: str):
        approval = self._store.get_approval_by_id(approval_id)
        if approval is None:
            raise ApprovalError(f"no such approval: {approval_id!r}")
        if approval.run_id != self._run_id:
            raise ApprovalError("approval belongs to a different run")
        return approval

    @staticmethod
    def _action_from_approval(approval) -> Action:
        return Action(
            name=approval.action_name,
            subject_id=approval.subject_id,
            idempotency_key=approval.idempotency_key,
            cost=approval.cost,
            params=json.loads(approval.params_json),
        )

    def _finish(
        self,
        action: Action,
        status: ExecutionStatus,
        *,
        result: Any = None,
        approval_id: str | None = None,
        detail: str = "",
    ) -> ExecutionResult:
        self._audit(action, status.value, detail=detail)
        return ExecutionResult(
            status=status,
            idempotency_key=action.idempotency_key,
            result=result,
            approval_id=approval_id,
            detail=detail,
        )

    def _audit(self, action: Action, decision: str, *, detail: str) -> None:
        self._store.append_audit(
            run_id=self._run_id,
            timestamp=self._now(),
            action_name=action.name,
            subject_id=action.subject_id,
            idempotency_key=action.idempotency_key,
            cost=action.cost,
            decision=decision,
            detail=detail,
        )
