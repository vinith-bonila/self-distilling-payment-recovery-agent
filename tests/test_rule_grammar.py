"""Valid rules parse and evaluate correctly against the payment schema."""
from __future__ import annotations

from agentcore.rules import Operator, parse_rule, evaluate


def test_conjunction_parses_and_evaluates(payment_schema) -> None:
    proposal = {
        "precondition": {
            "all": [
                {"field": "failure_reason", "op": "eq", "value": "insufficient_funds"},
                {"field": "amount_inr", "op": "gt", "value": 5000},
            ]
        },
        "action": {"name": "escalate_to_human", "params": {}},
    }
    rule = parse_rule(proposal, payment_schema)
    assert len(rule.precondition) == 2
    assert rule.action.name == "escalate_to_human"
    assert evaluate(rule, {"failure_reason": "insufficient_funds", "amount_inr": 6000})
    assert not evaluate(rule, {"failure_reason": "insufficient_funds", "amount_inr": 4000})
    assert not evaluate(rule, {"failure_reason": "card_declined", "amount_inr": 6000})


def test_in_operator(payment_schema) -> None:
    proposal = {
        "precondition": {
            "all": [
                {
                    "field": "failure_reason",
                    "op": "in",
                    "value": ["card_declined", "expired_card"],
                }
            ]
        },
        "action": {"name": "send_payment_link", "params": {}},
    }
    rule = parse_rule(proposal, payment_schema)
    assert rule.precondition[0].op is Operator.IN
    assert evaluate(rule, {"failure_reason": "card_declined"})
    assert not evaluate(rule, {"failure_reason": "insufficient_funds"})


def test_action_with_typed_params(payment_schema) -> None:
    proposal = {
        "precondition": {
            "all": [{"field": "failure_reason", "op": "eq", "value": "processing_error"}]
        },
        "action": {"name": "wait_and_retry", "params": {"delay_hours": 24}},
    }
    rule = parse_rule(proposal, payment_schema)
    assert rule.action.params == {"delay_hours": 24}


def test_missing_context_field_does_not_match(payment_schema) -> None:
    proposal = {
        "precondition": {
            "all": [{"field": "customer_failed_payments", "op": "ge", "value": 3}]
        },
        "action": {"name": "escalate_to_human", "params": {}},
    }
    rule = parse_rule(proposal, payment_schema)
    # Field absent from context (e.g. history not fetched) -> no match, no error.
    assert evaluate(rule, {"failure_reason": "card_declined"}) is False
