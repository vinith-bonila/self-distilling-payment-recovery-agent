"""Parse and validate a proposed rule against a schema.

Treats the proposal as untrusted input. Every structural and type check is
explicit, and any violation raises :class:`RuleValidationError` for the whole
proposal (never a partial accept). The proposal is a plain mapping; no part of
it is ever executed, compiled or templated.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from agentcore.rules.errors import RuleValidationError
from agentcore.rules.grammar import ALLOWED_OPERATORS, Operator, Predicate, Rule, RuleAction
from agentcore.rules.schema import FieldSpec, FieldType, ParamSpec, Schema

_TOP_LEVEL_KEYS = {"precondition", "action"}
_PREDICATE_KEYS = {"field", "op", "value"}


def parse_rule(proposal: object, schema: Schema) -> Rule:
    """Validate ``proposal`` against ``schema`` and return a typed :class:`Rule`.

    Raises :class:`RuleValidationError` on any violation.
    """
    if not isinstance(proposal, Mapping):
        raise RuleValidationError("rule must be an object")
    keys = set(proposal.keys())
    if keys != _TOP_LEVEL_KEYS:
        raise RuleValidationError(
            f"rule must have exactly keys {sorted(_TOP_LEVEL_KEYS)}, got {sorted(keys)}"
        )
    precondition = _parse_precondition(proposal["precondition"], schema)
    action = _parse_action(proposal["action"], schema)
    return Rule(precondition=precondition, action=action)


def _parse_precondition(node: object, schema: Schema) -> tuple[Predicate, ...]:
    if not isinstance(node, Mapping):
        raise RuleValidationError("precondition must be an object with an 'all' list")
    if set(node.keys()) != {"all"}:
        raise RuleValidationError("precondition must have exactly the key 'all'")
    predicates = node["all"]
    # Must be a list of predicate objects — never a string expression.
    if not isinstance(predicates, Sequence) or isinstance(predicates, (str, bytes)):
        raise RuleValidationError("precondition 'all' must be a list of predicates")
    if not (1 <= len(predicates) <= schema.max_predicates):
        raise RuleValidationError(
            f"precondition must have between 1 and {schema.max_predicates} predicates"
        )
    return tuple(_parse_predicate(p, schema) for p in predicates)


def _parse_predicate(node: object, schema: Schema) -> Predicate:
    if not isinstance(node, Mapping):
        raise RuleValidationError("predicate must be an object")
    if set(node.keys()) != _PREDICATE_KEYS:
        raise RuleValidationError(
            f"predicate must have exactly keys {sorted(_PREDICATE_KEYS)}, "
            f"got {sorted(node.keys())}"
        )
    field_name = node["field"]
    if not isinstance(field_name, str) or field_name not in schema.fields:
        raise RuleValidationError(f"unknown or invalid field: {field_name!r}")
    field_spec = schema.fields[field_name]
    op = _parse_operator(node["op"], field_spec.type)
    value = _parse_literal(node["value"], field_spec, op, schema)
    return Predicate(field=field_name, op=op, value=value)


def _parse_operator(raw: object, field_type: FieldType) -> Operator:
    if not isinstance(raw, str):
        raise RuleValidationError(f"operator must be a string, got {type(raw).__name__}")
    try:
        op = Operator(raw)
    except ValueError:
        raise RuleValidationError(f"unknown operator: {raw!r}") from None
    if op not in ALLOWED_OPERATORS[field_type]:
        raise RuleValidationError(
            f"operator {raw!r} not allowed for {field_type.value} field"
        )
    return op


def _parse_literal(
    value: object, spec: FieldSpec | ParamSpec, op: Operator, schema: Schema
) -> object:
    if op is Operator.IN:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise RuleValidationError("'in' operand must be a list")
        if not (1 <= len(value) <= schema.max_in_list):
            raise RuleValidationError(
                f"'in' list must have between 1 and {schema.max_in_list} items"
            )
        return tuple(_scalar(item, spec) for item in value)
    return _scalar(value, spec)


def _scalar(value: object, spec: FieldSpec | ParamSpec) -> object:
    """Validate a single scalar literal against a field/param type."""
    field_type = spec.type
    if field_type is FieldType.NUMBER:
        # bool is a subclass of int; exclude it explicitly.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuleValidationError(f"expected a number literal, got {value!r}")
        return value
    if field_type is FieldType.STRING:
        if not isinstance(value, str):
            raise RuleValidationError(f"expected a string literal, got {value!r}")
        return value
    if field_type is FieldType.BOOL:
        if not isinstance(value, bool):
            raise RuleValidationError(f"expected a bool literal, got {value!r}")
        return value
    if field_type is FieldType.ENUM:
        if not isinstance(value, str) or value not in spec.allowed_values:
            raise RuleValidationError(
                f"value {value!r} is not an allowed enum member"
            )
        return value
    raise RuleValidationError(f"unsupported field type: {field_type!r}")


def _parse_action(node: object, schema: Schema) -> RuleAction:
    if not isinstance(node, Mapping):
        raise RuleValidationError("action must be an object")
    keys = set(node.keys())
    if "name" not in keys or not keys <= {"name", "params"}:
        raise RuleValidationError("action must have 'name' and optionally 'params'")
    name = node["name"]
    if not isinstance(name, str) or name not in schema.actions:
        raise RuleValidationError(f"unknown or invalid action: {name!r}")
    action_spec = schema.actions[name]
    params = node.get("params", {})
    if not isinstance(params, Mapping):
        raise RuleValidationError("action params must be an object")
    if set(params.keys()) != set(action_spec.params.keys()):
        raise RuleValidationError(
            f"action {name!r} params must be exactly "
            f"{sorted(action_spec.params.keys())}, got {sorted(params.keys())}"
        )
    parsed: dict[str, object] = {}
    for param_name, param_spec in action_spec.params.items():
        # Parameters are literals only — a param can never reference a field.
        parsed[param_name] = _scalar(params[param_name], param_spec)
    return RuleAction(name=name, params=parsed)
