"""The grammar/validator is domain-neutral.

The same machinery that validates payment rules must work for a completely
unrelated (weather) schema, and must reject payment fields when given the
weather schema. This is the proof that ``agentcore.rules`` carries no payment
knowledge — more convincing than any prose in the README.
"""
from __future__ import annotations

import pytest

from agentcore.rules import (
    ActionSpec,
    FieldSpec,
    FieldType,
    ParamSpec,
    RuleValidationError,
    Schema,
    evaluate,
    parse_rule,
)


def _weather_schema() -> Schema:
    return Schema(
        fields={
            "temperature": FieldSpec(FieldType.NUMBER),
            "region": FieldSpec(FieldType.ENUM, frozenset({"north", "south"})),
            "is_holiday": FieldSpec(FieldType.BOOL),
        },
        actions={
            "alert": ActionSpec(
                "alert", {"channel": ParamSpec(FieldType.ENUM, frozenset({"sms", "email"}))}
            ),
            "ignore": ActionSpec("ignore"),
        },
    )


def test_non_payment_rule_parses_and_evaluates() -> None:
    schema = _weather_schema()
    proposal = {
        "precondition": {
            "all": [
                {"field": "temperature", "op": "gt", "value": 40},
                {"field": "region", "op": "eq", "value": "north"},
            ]
        },
        "action": {"name": "alert", "params": {"channel": "sms"}},
    }
    rule = parse_rule(proposal, schema)
    assert rule.action.params == {"channel": "sms"}
    assert evaluate(rule, {"temperature": 45, "region": "north"}) is True
    assert evaluate(rule, {"temperature": 30, "region": "north"}) is False
    assert evaluate(rule, {"temperature": 45, "region": "south"}) is False


def test_payment_field_rejected_under_weather_schema() -> None:
    schema = _weather_schema()
    proposal = {
        "precondition": {
            "all": [{"field": "failure_reason", "op": "eq", "value": "insufficient_funds"}]
        },
        "action": {"name": "ignore", "params": {}},
    }
    with pytest.raises(RuleValidationError):
        parse_rule(proposal, schema)


def test_bad_enum_param_rejected_under_weather_schema() -> None:
    schema = _weather_schema()
    proposal = {
        "precondition": {"all": [{"field": "temperature", "op": "gt", "value": 40}]},
        "action": {"name": "alert", "params": {"channel": "carrier_pigeon"}},
    }
    with pytest.raises(RuleValidationError):
        parse_rule(proposal, schema)
