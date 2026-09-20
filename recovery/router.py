"""The deterministic policy router.

Evaluates the active, versioned rules (in priority order) against a normalised
context. A matching rule resolves the case deterministically; if none match the
case is escalated to the agent/LLM. The router reasons only in terms of the
normalised ``FailureReason`` (and other normalised fields), never a provider
string — the grammar evaluator sees only the primitive context.
"""
from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentcore.rules import evaluate
from recovery.rules_repo import RuleRepository


class Route(enum.Enum):
    DETERMINISTIC = "deterministic"
    ESCALATE_TO_AGENT = "escalate_to_agent"


@dataclass(frozen=True)
class RoutingDecision:
    """Outcome of routing one event."""

    route: Route
    action: str | None = None
    action_params: Mapping[str, Any] | None = None
    rule_key: str | None = None
    rule_version: int | None = None


class PolicyRouter:
    """Resolves obvious failures via rules; escalates ambiguous ones."""

    def __init__(self, repo: RuleRepository) -> None:
        self._repo = repo

    def route(self, context: Mapping[str, Any]) -> RoutingDecision:
        """Return the first matching active rule's action, or escalate."""
        for loaded in self._repo.active_rules():
            if evaluate(loaded.rule, context):
                return RoutingDecision(
                    route=Route.DETERMINISTIC,
                    action=loaded.rule.action.name,
                    action_params=dict(loaded.rule.action.params),
                    rule_key=loaded.rule_key,
                    rule_version=loaded.version,
                )
        return RoutingDecision(route=Route.ESCALATE_TO_AGENT)
