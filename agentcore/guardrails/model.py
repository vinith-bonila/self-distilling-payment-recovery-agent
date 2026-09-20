"""Value objects for the guardrail layer.

All types here are domain-neutral. Money, payments and providers do not appear;
a caller in another layer decides that ``cost`` means rupees and that an
``Action`` named ``"refund"`` moves money.
"""
from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ExecutionStatus(enum.Enum):
    """Terminal outcome of a single :meth:`GuardedExecutor.execute` call."""

    EXECUTED = "executed"
    DUPLICATE = "duplicate"  # idempotent replay: stored result returned, no new effect
    PENDING_APPROVAL = "pending_approval"  # gated; awaiting or denied human approval
    REJECTED_ACTION_BUDGET = "rejected_action_budget"
    REJECTED_SPEND_CAP = "rejected_spend_cap"  # this call tripped the breaker
    REJECTED_BREAKER_OPEN = "rejected_breaker_open"  # breaker already tripped; run halted


class ApprovalState(enum.Enum):
    """Lifecycle state of a pending-approval record."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class GuardPolicy:
    """Configuration for a guarded run. Neutral units throughout.

    ``approval_threshold``: an action whose ``cost`` is strictly greater than
    this is routed to the approval queue and never auto-executed.
    ``per_subject_action_budget``: the maximum number of effects that may
    execute for any single subject in the run.
    ``per_run_spend_cap``: the maximum total ``cost`` of executed effects in the
    run; the call that would exceed it trips the breaker and halts the run.
    """

    approval_threshold: float
    per_subject_action_budget: int
    per_run_spend_cap: float


@dataclass(frozen=True)
class Action:
    """A declarative, side-effect-free description of an intended effect.

    An ``Action`` is pure data. It carries no callable and no approval flag, so
    there is deliberately no field a caller could set to self-authorise or to
    bypass a guard. The only way to cause its effect is to hand it to a
    :class:`GuardedExecutor`, which resolves a privately held handler.
    """

    name: str
    subject_id: str
    idempotency_key: str
    cost: float = 0.0
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    """What :meth:`GuardedExecutor.execute` returns."""

    status: ExecutionStatus
    idempotency_key: str
    result: Any = None
    approval_id: str | None = None
    detail: str = ""

    @property
    def executed(self) -> bool:
        """True iff a fresh effect was performed by this call."""
        return self.status is ExecutionStatus.EXECUTED


# --- persistence records -------------------------------------------------


@dataclass
class IdempotencyRecord:
    """A stored terminal outcome for one idempotency key (executed effects)."""

    run_id: str
    idempotency_key: str
    status: str
    result_json: str
    created_at: datetime


@dataclass
class ApprovalRecord:
    """A human-gated action awaiting or having received a decision."""

    approval_id: str
    run_id: str
    idempotency_key: str
    action_name: str
    subject_id: str
    cost: float
    params_json: str
    state: ApprovalState
    created_at: datetime
    decided_at: datetime | None = None


@dataclass(frozen=True)
class AuditEntry:
    """One append-only audit record. ``seq`` is monotonic within a run."""

    run_id: str
    seq: int
    timestamp: datetime
    action_name: str
    subject_id: str
    idempotency_key: str
    cost: float
    decision: str  # an ExecutionStatus value, or "error"
    detail: str = ""
