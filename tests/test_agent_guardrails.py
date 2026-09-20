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
# Refund takes no model-supplied arguments: the amount is server-derived.
_REFUND = '{"thought":"x","action":{"tool":"refund_payment","args":{}}}'


def _spy_refund(provider):
    calls = {"n": 0, "amounts": []}
    original = provider.refund_payment

    def spy(payment_id, amount_inr, idempotency_key):
        calls["n"] += 1
        calls["amounts"].append(amount_inr)
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
        ScriptedLLMClient([_REFUND, _FINAL]),
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
        ScriptedLLMClient([_REFUND, _REFUND, _FINAL]),
        settings(),
        store=store,
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case(amount=1000.0)))
    assert calls["n"] == 1  # double-fire -> exactly one real refund
    assert calls["amounts"] == [1000.0]  # server-derived amount
    decisions = [s.decision for s in trajectory.steps]
    assert "effect_executed" in decisions
    assert "effect_duplicate" in decisions


def test_model_supplied_refund_amount_is_rejected() -> None:
    # A malicious model tries to name an inflated amount; refund takes no args,
    # so the call is rejected and no refund happens at all.
    provider = provider_with_payment(amount=100.0)
    calls = _spy_refund(provider)
    store = InMemoryGuardStore()
    malicious = '{"thought":"x","action":{"tool":"refund_payment","args":{"amount_inr":999999}}}'
    loop = build_agent(
        case(amount=100.0),
        provider,
        ScriptedLLMClient([malicious, _FINAL]),
        settings(approval_threshold_inr=1_000_000.0),  # would not gate 999999
        store=store,
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case(amount=100.0)))
    assert trajectory.steps[0].decision == "rejected_invalid_args"
    assert calls["n"] == 0  # no refund executed
    assert len(loop.executor.pending_approvals()) == 0


def test_refund_uses_authoritative_amount_not_event_or_model() -> None:
    # Authoritative payment record says 9000; the case/event says 100. The
    # refund and the approval record must use 9000 (from the provider), and the
    # model cannot influence it.
    provider = provider_with_payment(amount=9000.0)
    calls = _spy_refund(provider)
    store = InMemoryGuardStore()
    loop = build_agent(
        case(amount=100.0),  # event/case amount is deliberately different
        provider,
        ScriptedLLMClient([_REFUND, _FINAL]),
        settings(approval_threshold_inr=5000.0),
        store=store,
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case(amount=100.0)))
    assert trajectory.outcome is AgentOutcome.PENDING_APPROVAL
    pending = loop.executor.pending_approvals()
    assert len(pending) == 1
    assert pending[0].cost == 9000.0  # authoritative amount, not 100 or a model value
    assert calls["n"] == 0  # still gated, not executed


def test_approval_record_holds_authoritative_amount_when_executed() -> None:
    # Below threshold: refund executes with the server-derived amount, and the
    # guarded executor's audit records that exact amount.
    provider = provider_with_payment(amount=3000.0)
    calls = _spy_refund(provider)
    store = InMemoryGuardStore()
    loop = build_agent(
        case(amount=1.0),  # event/case amount irrelevant
        provider,
        ScriptedLLMClient([_REFUND, _FINAL]),
        settings(approval_threshold_inr=5000.0),
        store=store,
    )
    loop.run(subject_id="cust_1", case=case_view(case(amount=1.0)))
    assert calls["amounts"] == [3000.0]  # server-derived
    audit = store.read_audit("pay_1")
    refund_entries = [e for e in audit if e.action_name == "refund"]
    assert refund_entries and refund_entries[-1].cost == 3000.0


def test_spend_cap_breaker_halts_refund() -> None:
    provider = provider_with_payment(amount=1000.0)
    calls = _spy_refund(provider)
    store = InMemoryGuardStore()
    loop = build_agent(
        case(amount=1000.0),
        provider,
        ScriptedLLMClient([_REFUND, _FINAL]),
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
