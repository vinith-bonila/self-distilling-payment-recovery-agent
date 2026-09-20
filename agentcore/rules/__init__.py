"""Domain-neutral rule grammar, validator and evaluator.

Security-critical. LLM-proposed rules are *data*, never code:

* A rule is parsed from a plain mapping (JSON-shaped) into typed objects.
* A precondition is a conjunction of whitelisted predicates over whitelisted
  context fields, each with a typed literal operand.
* An action is one member of a fixed action set with typed parameters.
* Anything that does not fit the grammar is rejected wholesale on ingest.
* Evaluation compares primitives through a fixed operator dispatch table.
  There is no ``eval``, ``exec``, ``compile``, string templating or attribute
  access on untrusted input anywhere in this package.

The grammar machinery is parameterised by a :class:`Schema` supplied by the
caller (which fields exist, their types, and the action set), so it carries no
payment or provider knowledge. ``recovery`` injects the payment schema;
``tests`` inject a non-payment schema to prove neutrality.
"""
from __future__ import annotations

from agentcore.rules.errors import RuleValidationError
from agentcore.rules.evaluator import evaluate
from agentcore.rules.grammar import Operator, Predicate, Rule, RuleAction
from agentcore.rules.schema import (
    ActionSpec,
    FieldSpec,
    FieldType,
    ParamSpec,
    Schema,
)
from agentcore.rules.validator import parse_rule

__all__ = [
    "ActionSpec",
    "FieldSpec",
    "FieldType",
    "Operator",
    "ParamSpec",
    "Predicate",
    "Rule",
    "RuleAction",
    "RuleValidationError",
    "Schema",
    "evaluate",
    "parse_rule",
]
