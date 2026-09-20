"""Replay the append-only audit log to reconstruct run state.

The audit log is the source of truth: reading it in ``seq`` order reproduces
what happened, which is what "replayable audit log" means. This module is how
the tests prove the reconstructed state matches the store's own counters.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from agentcore.guardrails.model import AuditEntry, ExecutionStatus


@dataclass
class ReplayState:
    """State reconstructed purely from a run's audit entries."""

    executed_count: int = 0
    total_cost: float = 0.0
    per_subject_executed: dict[str, int] = field(default_factory=dict)
    decisions: dict[str, int] = field(default_factory=dict)
    breaker_tripped: bool = False


def reconstruct(entries: Iterable[AuditEntry]) -> ReplayState:
    """Fold audit ``entries`` (any order; sorted internally) into a state.

    Only ``EXECUTED`` entries move spend and budget, mirroring the executor.
    """
    state = ReplayState()
    for entry in sorted(entries, key=lambda e: e.seq):
        state.decisions[entry.decision] = state.decisions.get(entry.decision, 0) + 1
        if entry.decision == ExecutionStatus.REJECTED_SPEND_CAP.value:
            state.breaker_tripped = True
        if entry.decision == ExecutionStatus.EXECUTED.value:
            state.executed_count += 1
            state.total_cost += entry.cost
            state.per_subject_executed[entry.subject_id] = (
                state.per_subject_executed.get(entry.subject_id, 0) + 1
            )
    return state
