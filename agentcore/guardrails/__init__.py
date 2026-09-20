"""Domain-neutral execution guardrails.

The centrepiece is :class:`~agentcore.guardrails.executor.GuardedExecutor`: the
*only* path through which a side effect can occur. An :class:`Action` is pure
data (no callable, no self-authorisation field); effects are registered
handlers held privately by the executor. Every effect therefore passes, in
order, through:

1. the spend-cap breaker (halts the whole run once tripped),
2. persisted idempotency (a replayed key returns the stored result, never a
   second effect),
3. the approval gate (cost above threshold routes to a pending queue and does
   not execute until a human approves),
4. the per-subject action budget,
5. the per-run spend cap,

and every call appends one entry to an append-only, replayable audit log.

Nothing here knows about payments, providers, LLMs or recovery. The vocabulary
is deliberately generic: subject, action, effect, cost, approval.
"""
from __future__ import annotations

from agentcore.guardrails.errors import (
    ApprovalError,
    GuardError,
    UnknownActionError,
)
from agentcore.guardrails.executor import EffectHandler, GuardedExecutor
from agentcore.guardrails.model import (
    Action,
    ApprovalRecord,
    ApprovalState,
    AuditEntry,
    ExecutionResult,
    ExecutionStatus,
    GuardPolicy,
    IdempotencyRecord,
)
from agentcore.guardrails.replay import ReplayState, reconstruct
from agentcore.guardrails.store import (
    GuardStore,
    InMemoryGuardStore,
    SqlGuardStore,
)

__all__ = [
    "Action",
    "ApprovalError",
    "ApprovalRecord",
    "ApprovalState",
    "AuditEntry",
    "EffectHandler",
    "ExecutionResult",
    "ExecutionStatus",
    "GuardError",
    "GuardPolicy",
    "GuardStore",
    "GuardedExecutor",
    "IdempotencyRecord",
    "InMemoryGuardStore",
    "ReplayState",
    "SqlGuardStore",
    "UnknownActionError",
    "reconstruct",
]
