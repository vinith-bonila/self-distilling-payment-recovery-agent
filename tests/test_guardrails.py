"""Guardrail behaviour, proven against both store implementations.

Every behaviour test runs against the in-memory store *and* the durable SQL
store (the ``store`` fixture is parametrised), so the two are proven to behave
identically. Structural tests and the cross-restart persistence test stand
alone.
"""
from __future__ import annotations

from dataclasses import fields

import pytest

from agentcore.guardrails import (
    Action,
    ApprovalError,
    ExecutionStatus,
    GuardedExecutor,
    GuardPolicy,
    InMemoryGuardStore,
    SqlGuardStore,
    UnknownActionError,
    reconstruct,
)

RUN = "run-1"
HIGH = 1_000_000_000.0  # effectively-infinite threshold/cap when a test wants one apart


class Counter:
    """Counts real effect invocations so 'exactly one effect' is verifiable."""

    def __init__(self) -> None:
        self.calls = 0

    def handler(self, action: Action) -> dict:
        self.calls += 1
        return {"ok": True, "key": action.idempotency_key, "call": self.calls}


def build(
    store,
    *,
    policy: GuardPolicy | None = None,
    run_id: str = RUN,
) -> tuple[GuardedExecutor, Counter]:
    policy = policy or GuardPolicy(
        approval_threshold=HIGH, per_subject_action_budget=100, per_run_spend_cap=HIGH
    )
    executor = GuardedExecutor(run_id, store, policy)
    counter = Counter()
    executor.register("act", counter.handler)
    return executor, counter


@pytest.fixture(params=["memory", "sql"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryGuardStore()
    return SqlGuardStore(f"sqlite:///{(tmp_path / 'guard.db').as_posix()}")


# --- happy path & idempotency -------------------------------------------


def test_execute_records_spend_and_budget(store) -> None:
    executor, counter = build(store)
    result = executor.execute(Action("act", "s", "k1", cost=42))
    assert result.status is ExecutionStatus.EXECUTED
    assert counter.calls == 1
    assert store.accumulated_spend(RUN) == 42
    assert store.subject_executed_count(RUN, "s") == 1


def test_idempotent_replay_produces_exactly_one_effect(store) -> None:
    executor, counter = build(store)
    first = executor.execute(Action("act", "s", "k1", cost=1))
    second = executor.execute(Action("act", "s", "k1", cost=1))
    assert first.status is ExecutionStatus.EXECUTED
    assert second.status is ExecutionStatus.DUPLICATE
    assert counter.calls == 1  # double-fire, one effect
    assert second.result == first.result  # stored result replayed
    assert store.accumulated_spend(RUN) == 1  # spend not double-counted


# --- approval gate & bypass attempts ------------------------------------


def _approval_policy() -> GuardPolicy:
    return GuardPolicy(
        approval_threshold=100.0, per_subject_action_budget=100, per_run_spend_cap=HIGH
    )


def test_above_threshold_enters_pending_and_does_not_execute(store) -> None:
    executor, counter = build(store, policy=_approval_policy())
    result = executor.execute(Action("act", "s", "k1", cost=500))
    assert result.status is ExecutionStatus.PENDING_APPROVAL
    assert result.approval_id is not None
    assert counter.calls == 0
    assert len(executor.pending_approvals()) == 1


def test_at_threshold_executes_above_pends(store) -> None:
    executor, counter = build(
        store,
        policy=GuardPolicy(
            approval_threshold=1000.0,
            per_subject_action_budget=100,
            per_run_spend_cap=HIGH,
        ),
    )
    assert executor.execute(Action("act", "s", "k1", cost=1000)).status is (
        ExecutionStatus.EXECUTED
    )
    assert executor.execute(Action("act", "s", "k2", cost=1000.01)).status is (
        ExecutionStatus.PENDING_APPROVAL
    )


def test_repeated_execute_never_bypasses_pending(store) -> None:
    executor, counter = build(store, policy=_approval_policy())
    for _ in range(3):
        result = executor.execute(Action("act", "s", "k1", cost=500))
        assert result.status is ExecutionStatus.PENDING_APPROVAL
    assert counter.calls == 0
    assert len(executor.pending_approvals()) == 1  # no duplicate pending records


def test_params_cannot_self_authorise(store) -> None:
    executor, counter = build(store, policy=_approval_policy())
    result = executor.execute(
        Action(
            "act",
            "s",
            "k1",
            cost=500,
            params={"approved": True, "authorized": True, "bypass": True},
        )
    )
    assert result.status is ExecutionStatus.PENDING_APPROVAL
    assert counter.calls == 0


def test_approve_unknown_id_rejected(store) -> None:
    executor, counter = build(store, policy=_approval_policy())
    with pytest.raises(ApprovalError):
        executor.approve("apr_does_not_exist")
    assert counter.calls == 0


def test_approve_from_wrong_run_rejected(store) -> None:
    policy = _approval_policy()
    ex_a, ca = build(store, policy=policy, run_id="run-A")
    ex_b, cb = build(store, policy=policy, run_id="run-B")
    pending = ex_a.execute(Action("act", "s", "kA", cost=500))
    assert pending.status is ExecutionStatus.PENDING_APPROVAL
    with pytest.raises(ApprovalError):
        ex_b.approve(pending.approval_id)
    assert ca.calls == 0 and cb.calls == 0


def test_approval_executes_exactly_once(store) -> None:
    executor, counter = build(store, policy=_approval_policy())
    pending = executor.execute(Action("act", "s", "k1", cost=500))
    approved = executor.approve(pending.approval_id)
    assert approved.status is ExecutionStatus.EXECUTED
    assert counter.calls == 1
    again = executor.approve(pending.approval_id)  # re-approve is idempotent
    assert again.status is ExecutionStatus.DUPLICATE
    assert counter.calls == 1


def test_rejected_approval_never_executes(store) -> None:
    executor, counter = build(store, policy=_approval_policy())
    pending = executor.execute(Action("act", "s", "k1", cost=500))
    executor.reject_approval(pending.approval_id)
    after = executor.execute(Action("act", "s", "k1", cost=500))
    assert after.status is ExecutionStatus.PENDING_APPROVAL  # gated, not executed
    assert counter.calls == 0


# --- per-subject action budget ------------------------------------------


def test_action_budget_exhaustion(store) -> None:
    policy = GuardPolicy(
        approval_threshold=HIGH, per_subject_action_budget=2, per_run_spend_cap=HIGH
    )
    executor, counter = build(store, policy=policy)
    assert executor.execute(Action("act", "s1", "k1", cost=1)).status is (
        ExecutionStatus.EXECUTED
    )
    assert executor.execute(Action("act", "s1", "k2", cost=1)).status is (
        ExecutionStatus.EXECUTED
    )
    third = executor.execute(Action("act", "s1", "k3", cost=1))
    assert third.status is ExecutionStatus.REJECTED_ACTION_BUDGET
    assert counter.calls == 2
    # A different subject has its own budget.
    assert executor.execute(Action("act", "s2", "k4", cost=1)).status is (
        ExecutionStatus.EXECUTED
    )
    assert counter.calls == 3


# --- per-run spend-cap breaker ------------------------------------------


def test_spend_cap_breaker_trips_and_halts_run(store) -> None:
    policy = GuardPolicy(
        approval_threshold=HIGH, per_subject_action_budget=100, per_run_spend_cap=100.0
    )
    executor, counter = build(store, policy=policy)
    assert executor.execute(Action("act", "s1", "k1", cost=60)).status is (
        ExecutionStatus.EXECUTED
    )
    tripped = executor.execute(Action("act", "s1", "k2", cost=60))
    assert tripped.status is ExecutionStatus.REJECTED_SPEND_CAP
    # Run is halted: even a cheap action for a fresh subject is refused.
    halted = executor.execute(Action("act", "s2", "k3", cost=1))
    assert halted.status is ExecutionStatus.REJECTED_BREAKER_OPEN
    assert counter.calls == 1
    assert store.is_breaker_open(RUN) is True


def test_spend_cap_boundary_at_cap_ok_above_trips(store) -> None:
    policy = GuardPolicy(
        approval_threshold=HIGH, per_subject_action_budget=100, per_run_spend_cap=100.0
    )
    executor, counter = build(store, policy=policy)
    assert executor.execute(Action("act", "s", "k1", cost=100)).status is (
        ExecutionStatus.EXECUTED
    )  # exactly at cap is allowed
    over = executor.execute(Action("act", "s", "k2", cost=0.01))
    assert over.status is ExecutionStatus.REJECTED_SPEND_CAP
    assert counter.calls == 1


# --- append-only, replayable audit log ----------------------------------


def test_audit_is_append_only(store) -> None:
    assert hasattr(store, "append_audit") and hasattr(store, "read_audit")
    for banned in ("update_audit", "delete_audit", "remove_audit", "clear_audit"):
        assert not hasattr(store, banned), f"audit must be append-only: {banned}"
    executor, _ = build(store)
    executor.execute(Action("act", "s", "k1", cost=1))
    first = store.read_audit(RUN)
    executor.execute(Action("act", "s", "k2", cost=1))
    second = store.read_audit(RUN)
    assert len(second) == len(first) + 1
    assert second[: len(first)] == first  # earlier entries are immutable
    assert [e.seq for e in second] == list(range(1, len(second) + 1))


def test_audit_log_replays_to_store_state(store) -> None:
    executor, _ = build(store)
    executor.execute(Action("act", "s1", "k1", cost=10))
    executor.execute(Action("act", "s1", "k2", cost=20))
    executor.execute(Action("act", "s2", "k3", cost=5))
    executor.execute(Action("act", "s1", "k1", cost=10))  # duplicate

    state = reconstruct(store.read_audit(RUN))
    assert state.executed_count == 3
    assert state.total_cost == 35
    assert state.per_subject_executed == {"s1": 2, "s2": 1}
    assert state.decisions["duplicate"] == 1
    # Reconstructed-from-log state matches the store's own counters.
    assert store.accumulated_spend(RUN) == 35
    assert store.subject_executed_count(RUN, "s1") == 2


# --- structural: the guarded path is the only path ----------------------


def test_action_is_pure_data() -> None:
    names = {f.name for f in fields(Action)}
    assert names == {"name", "subject_id", "idempotency_key", "cost", "params"}
    # No field lets a caller carry a callable or self-authorise.
    assert not (names & {"effect", "callable", "handler", "approved", "authorized"})


def test_no_public_handler_accessor() -> None:
    executor, _ = build(InMemoryGuardStore())
    public = [n for n in dir(executor) if not n.startswith("_")]
    assert {"register", "execute", "approve"}.issubset(public)
    # Handlers are private (name-mangled); no public attribute exposes them.
    assert not any(n.lower().endswith("handlers") for n in public)
    assert hasattr(executor, "_GuardedExecutor__handlers")


def test_unknown_action_is_refused_not_run() -> None:
    executor, counter = build(InMemoryGuardStore())
    with pytest.raises(UnknownActionError):
        executor.execute(Action("unregistered", "s", "k1", cost=1))
    assert counter.calls == 0


# --- persisted & replay-safe across a simulated restart -----------------


def test_persisted_idempotency_survives_restart(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'persist.db').as_posix()}"
    policy = GuardPolicy(
        approval_threshold=HIGH, per_subject_action_budget=100, per_run_spend_cap=HIGH
    )

    store1 = SqlGuardStore(url)
    ex1 = GuardedExecutor("run-x", store1, policy)
    c1 = Counter()
    ex1.register("act", c1.handler)
    first = ex1.execute(Action("act", "s", "k1", cost=10))
    assert first.status is ExecutionStatus.EXECUTED
    assert c1.calls == 1

    # A brand-new store + executor on the same DB simulates a process restart.
    store2 = SqlGuardStore(url)
    ex2 = GuardedExecutor("run-x", store2, policy)
    c2 = Counter()
    ex2.register("act", c2.handler)
    second = ex2.execute(Action("act", "s", "k1", cost=10))
    assert second.status is ExecutionStatus.DUPLICATE
    assert c2.calls == 0  # no second effect after restart
    assert second.result == first.result
    assert store2.accumulated_spend("run-x") == 10  # spend not double-counted
    assert len(store2.read_audit("run-x")) == 2  # audit persisted across restart
