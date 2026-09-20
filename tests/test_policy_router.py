"""The policy router resolves obvious failures and escalates ambiguous ones."""
from __future__ import annotations

from datetime import datetime, timezone

from providers.types import EventType, FailureReason, NormalisedEvent
from recovery.context import build_context
from recovery.router import PolicyRouter, Route
from recovery.rules_repo import RuleStatus
from recovery.seed_rules import seed


def test_matches_seed_rule_deterministically(repo) -> None:
    seed(repo)
    router = PolicyRouter(repo)
    decision = router.route({"failure_reason": "risk_blocked", "amount_inr": 100.0})
    assert decision.route is Route.DETERMINISTIC
    assert decision.action == "escalate_to_human"
    assert decision.rule_key == "seed-risk-blocked-escalate"
    assert decision.rule_version == 1


def test_escalates_when_no_rule_matches(repo) -> None:
    seed(repo)
    router = PolicyRouter(repo)
    decision = router.route({"failure_reason": "insufficient_funds", "amount_inr": 5000.0})
    assert decision.route is Route.ESCALATE_TO_AGENT
    assert decision.action is None


def test_router_consumes_normalised_reason(repo) -> None:
    seed(repo)
    router = PolicyRouter(repo)
    event = NormalisedEvent(
        provider="razorpay",
        event_id="evt_1",
        event_type=EventType.PAYMENT_FAILED,
        payment_id="pay_1",
        amount_inr=250.0,
        failure_reason=FailureReason.RISK_BLOCKED,
        occurred_at=datetime.now(timezone.utc),
    )
    context = build_context(event)
    assert context["failure_reason"] == "risk_blocked"  # enum value, not a provider string
    decision = router.route(context)
    assert decision.route is Route.DETERMINISTIC
    assert decision.action == "escalate_to_human"


def test_priority_orders_matching_rules(repo) -> None:
    # Two rules that both match; the lower priority number wins.
    high_priority = {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 0}]},
        "action": {"name": "escalate_to_human", "params": {}},
    }
    low_priority = {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 0}]},
        "action": {"name": "no_action", "params": {}},
    }
    repo.add_rule("wins", high_priority, status=RuleStatus.ACTIVE, priority=5)
    repo.add_rule("loses", low_priority, status=RuleStatus.ACTIVE, priority=50)
    decision = PolicyRouter(repo).route({"amount_inr": 100.0})
    assert decision.rule_key == "wins"
    assert decision.action == "escalate_to_human"
