"""Guardrail enforcement through the agent loop, and no-bypass proof."""
from __future__ import annotations

from _agent_support import (
    ScriptedLLMClient,
    case,
    case_view,
    provider_with_payment,
    settings,
)

from agentcore.guardrails import InMemoryGuardStore
from agentcore.trajectory import AgentOutcome
from recovery.agent import build_agent
from recovery.tools import build_tools

_FINAL = '{"thought":"done","final":{"resolution":"refund"}}'


def _refund(amount: int) -> str:
    return f'{{"thought":"x","action":{{"tool":"refund_payment","args":{{"amount_inr":{amount}}}}}}}'


def _spy_refund(provider):
    calls = {"n": 0}
    original = provider.refund_payment

    def spy(payment_id, amount_inr, idempotency_key):
        calls["n"] += 1
        return original(payment_id, amount_inr, idempotency_key)

    provider.refund_payment = spy  # type: ignore[method-assign]
    return calls


def test_refund_above_threshold_hits_approval_gate() -> None:
    provider = provider_with_payment(amount=9000.0)
    calls = _spy_refund(provider)
    store = InMemoryGuardStore()
    loop = build_agent(
        case(amount=9000.0),
        provider,
        ScriptedLLMClient([_refund(9000), _FINAL]),
        settings(approval_threshold_inr=5000.0),
        store=store,
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case(amount=9000.0)))
    assert trajectory.outcome is AgentOutcome.PENDING_APPROVAL
    assert calls["n"] == 0  # provider refund never called while pending
    assert len(loop.executor.pending_approvals()) == 1


def test_refund_below_threshold_executes_once_and_is_idempotent() -> None:
    provider = provider_with_payment(amount=1000.0)
    calls = _spy_refund(provider)
    store = InMemoryGuardStore()
    loop = build_agent(
        case(amount=1000.0),
        provider,
        ScriptedLLMClient([_refund(1000), _refund(1000), _FINAL]),
        settings(),
        store=store,
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case(amount=1000.0)))
    assert calls["n"] == 1  # double-fire -> exactly one real refund
    decisions = [s.decision for s in trajectory.steps]
    assert "effect_executed" in decisions
    assert "effect_duplicate" in decisions


def test_spend_cap_breaker_halts_refund() -> None:
    provider = provider_with_payment(amount=1000.0)
    calls = _spy_refund(provider)
    store = InMemoryGuardStore()
    loop = build_agent(
        case(amount=1000.0),
        provider,
        ScriptedLLMClient([_refund(1000), _FINAL]),
        settings(per_run_spend_cap_inr=500.0, approval_threshold_inr=100000.0),
        store=store,
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case(amount=1000.0)))
    assert calls["n"] == 0  # never executed; breaker tripped
    assert any(s.decision == "effect_rejected_spend_cap" for s in trajectory.steps)
    assert trajectory.outcome is AgentOutcome.REJECTED


def test_action_budget_limits_effects() -> None:
    responses = [
        '{"thought":"x","action":{"tool":"escalate_to_human","args":{}}}',
        '{"thought":"x","action":{"tool":"create_payment_link","args":{"amount_inr":100}}}',
        '{"thought":"done","final":{"resolution":null}}',
    ]
    loop = build_agent(
        case(amount=100.0),
        provider_with_payment(amount=100.0),
        ScriptedLLMClient(responses),
        settings(per_subject_action_budget=1),
        store=InMemoryGuardStore(),
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case(amount=100.0)))
    decisions = [s.decision for s in trajectory.steps]
    assert "effect_executed" in decisions
    assert "effect_rejected_action_budget" in decisions


def test_effect_tools_have_no_direct_handler() -> None:
    registry, _ = build_tools(case(), provider_with_payment())
    for name in ("create_payment_link", "refund_payment", "escalate_to_human"):
        spec = registry.get(name)
        assert spec is not None and spec.is_effect
        assert spec.read_handler is None and spec.build_action is not None
    for name in ("fetch_payment", "fetch_order", "fetch_customer_history"):
        spec = registry.get(name)
        assert spec is not None and not spec.is_effect
        assert spec.read_handler is not None and spec.build_action is None


def test_side_effect_only_happens_through_the_executor() -> None:
    # A create_payment_link must appear in the guarded executor's audit log,
    # proving it went through execute() and not around it.
    provider = provider_with_payment()
    store = InMemoryGuardStore()
    responses = [
        '{"thought":"x","action":{"tool":"create_payment_link","args":{"amount_inr":2500}}}',
        '{"thought":"done","final":{"resolution":"send_payment_link"}}',
    ]
    loop = build_agent(case(), provider, ScriptedLLMClient(responses), settings(), store=store)
    loop.run(subject_id="cust_1", case=case_view(case()))
    audit = store.read_audit("pay_1")
    assert any(
        e.decision == "executed" and e.action_name == "send_payment_link" for e in audit
    )
