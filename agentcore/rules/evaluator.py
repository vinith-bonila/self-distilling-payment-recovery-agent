"""Evaluate a validated rule against a context of primitives.

Comparisons go through a fixed operator dispatch table over the standard
``operator`` module — no ``eval``/``exec``, no dynamic attribute access. A
missing context field, or a type mismatch at comparison time, makes the
predicate false (the rule simply does not match) rather than raising.
"""
from __future__ import annotations

import operator
from collections.abc import Mapping

from agentcore.rules.grammar import Operator, Rule

_DISPATCH = {
    Operator.EQ: operator.eq,
    Operator.NE: operator.ne,
    Operator.LT: operator.lt,
    Operator.LE: operator.le,
    Operator.GT: operator.gt,
    Operator.GE: operator.ge,
    Operator.IN: lambda actual, allowed: actual in allowed,
}


def evaluate(rule: Rule, context: Mapping[str, object]) -> bool:
    """Return ``True`` iff every predicate holds for ``context`` (conjunction)."""
    for predicate in rule.precondition:
        if predicate.field not in context:
            return False
        actual = context[predicate.field]
        compare = _DISPATCH[predicate.op]
        try:
            if not compare(actual, predicate.value):
                return False
        except TypeError:
            # Incomparable types (e.g. number operator against a string) simply
            # fail to match; they never raise out of evaluation.
            return False
    return True
