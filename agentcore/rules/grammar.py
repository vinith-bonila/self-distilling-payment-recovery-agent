"""Typed, validated rule objects and the operator set.

These are only ever produced by :func:`agentcore.rules.validator.parse_rule`.
A ``Rule`` holds primitives (numbers, strings, bools, tuples of those): it is
inert data, never anything callable or executable.
"""
from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass

from agentcore.rules.schema import FieldType


class Operator(enum.Enum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IN = "in"


# Which operators are permitted for each field type. Ordering operators are
# only allowed on numbers; enums/strings/bools support (in)equality (and IN).
ALLOWED_OPERATORS: dict[FieldType, frozenset[Operator]] = {
    FieldType.NUMBER: frozenset(
        {Operator.EQ, Operator.NE, Operator.LT, Operator.LE, Operator.GT, Operator.GE, Operator.IN}
    ),
    FieldType.STRING: frozenset({Operator.EQ, Operator.NE, Operator.IN}),
    FieldType.BOOL: frozenset({Operator.EQ, Operator.NE}),
    FieldType.ENUM: frozenset({Operator.EQ, Operator.NE, Operator.IN}),
}


@dataclass(frozen=True)
class Predicate:
    """``field <op> value``. ``value`` is a scalar, or a tuple for ``IN``."""

    field: str
    op: Operator
    value: object


@dataclass(frozen=True)
class RuleAction:
    """One action from the schema with its validated, typed parameters."""

    name: str
    params: Mapping[str, object]


@dataclass(frozen=True)
class Rule:
    """A validated rule: a conjunction of predicates and a single action."""

    precondition: tuple[Predicate, ...]
    action: RuleAction
