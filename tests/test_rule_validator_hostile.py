"""Hostile / malformed rule proposals are rejected wholesale by the validator.

Every case here is the kind of thing an adversarial or confused LLM might emit.
None may parse; all must raise RuleValidationError. This is the trust boundary
for LLM output — treated as untrusted data, never executed.
"""
from __future__ import annotations

import pytest

from agentcore.rules import RuleValidationError, parse_rule

HOSTILE_PROPOSALS = {
    "rule_is_not_an_object": ["not", "a", "rule"],
    "precondition_is_a_string": {
        "precondition": "amount_inr > 5000 or __import__('os').system('rm -rf /')",
        "action": {"name": "no_action", "params": {}},
    },
    "all_is_a_string_expression": {
        "precondition": {"all": "amount_inr > 5000"},
        "action": {"name": "no_action", "params": {}},
    },
    "unknown_field": {
        "precondition": {"all": [{"field": "__import__", "op": "eq", "value": "os"}]},
        "action": {"name": "no_action", "params": {}},
    },
    "unknown_operator": {
        "precondition": {"all": [{"field": "amount_inr", "op": "exec", "value": 1}]},
        "action": {"name": "no_action", "params": {}},
    },
    "operator_not_allowed_for_enum": {
        "precondition": {
            "all": [{"field": "failure_reason", "op": "gt", "value": "insufficient_funds"}]
        },
        "action": {"name": "no_action", "params": {}},
    },
    "op_not_a_string": {
        "precondition": {"all": [{"field": "amount_inr", "op": 1, "value": 1}]},
        "action": {"name": "no_action", "params": {}},
    },
    "string_literal_for_number_field": {
        "precondition": {
            "all": [{"field": "amount_inr", "op": "eq", "value": "5000; DROP TABLE rules"}]
        },
        "action": {"name": "no_action", "params": {}},
    },
    "bool_literal_for_number_field": {
        "precondition": {"all": [{"field": "amount_inr", "op": "eq", "value": True}]},
        "action": {"name": "no_action", "params": {}},
    },
    "nonscalar_literal": {
        "precondition": {
            "all": [{"field": "amount_inr", "op": "eq", "value": {"__class__": "x"}}]
        },
        "action": {"name": "no_action", "params": {}},
    },
    "list_literal_for_eq": {
        "precondition": {"all": [{"field": "amount_inr", "op": "eq", "value": [1, 2]}]},
        "action": {"name": "no_action", "params": {}},
    },
    "in_operand_not_a_list": {
        "precondition": {
            "all": [{"field": "failure_reason", "op": "in", "value": "card_declined"}]
        },
        "action": {"name": "no_action", "params": {}},
    },
    "enum_value_not_allowed": {
        "precondition": {
            "all": [{"field": "failure_reason", "op": "eq", "value": "delete_everything"}]
        },
        "action": {"name": "no_action", "params": {}},
    },
    "unknown_action": {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 1}]},
        "action": {"name": "run_shell", "params": {"cmd": "rm -rf /"}},
    },
    "action_is_a_string": {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 1}]},
        "action": "escalate_to_human",
    },
    "extra_action_param": {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 1}]},
        "action": {"name": "escalate_to_human", "params": {"cmd": "x"}},
    },
    "wrong_param_type": {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 1}]},
        "action": {"name": "wait_and_retry", "params": {"delay_hours": "soon"}},
    },
    "missing_required_param": {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 1}]},
        "action": {"name": "wait_and_retry", "params": {}},
    },
    "extra_top_level_key": {
        "precondition": {"all": [{"field": "amount_inr", "op": "gt", "value": 1}]},
        "action": {"name": "no_action", "params": {}},
        "eval": "__import__('os')",
    },
    "predicate_extra_key": {
        "precondition": {
            "all": [
                {
                    "field": "amount_inr",
                    "op": "gt",
                    "value": 1,
                    "python": "os.system('x')",
                }
            ]
        },
        "action": {"name": "no_action", "params": {}},
    },
    "empty_precondition": {
        "precondition": {"all": []},
        "action": {"name": "no_action", "params": {}},
    },
    "wrong_precondition_key": {
        "precondition": {"any": [{"field": "amount_inr", "op": "gt", "value": 1}]},
        "action": {"name": "no_action", "params": {}},
    },
    "too_many_predicates": {
        "precondition": {
            "all": [{"field": "amount_inr", "op": "gt", "value": i} for i in range(50)]
        },
        "action": {"name": "no_action", "params": {}},
    },
}


@pytest.mark.parametrize("name", sorted(HOSTILE_PROPOSALS))
def test_hostile_proposal_is_rejected(name, payment_schema) -> None:
    with pytest.raises(RuleValidationError):
        parse_rule(HOSTILE_PROPOSALS[name], payment_schema)
