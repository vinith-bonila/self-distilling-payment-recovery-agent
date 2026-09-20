"""Hand-written seed rules for obvious, unambiguous failure reasons.

These are business defaults, not derived from any ground truth: they encode the
prior that a risk block always needs a human, and that invalid details always
need the customer to re-enter them. The eval measures their real outcomes; the
distillation loop (Phase 7) grows coverage into the context-dependent cases.

Keeping this set tiny is deliberate (decision B): the pre-distillation baseline
starts with a high-but-realistic LLM share, so distillation has room to move it.
"""
from __future__ import annotations

from typing import Any

from recovery.actions import RecoveryAction
from recovery.rules_repo import RuleRepository, RuleStatus

SEED_RULES: list[dict[str, Any]] = [
    {
        "rule_key": "seed-risk-blocked-escalate",
        "priority": 10,
        "definition": {
            "precondition": {
                "all": [{"field": "failure_reason", "op": "eq", "value": "risk_blocked"}]
            },
            "action": {"name": RecoveryAction.ESCALATE_TO_HUMAN.value, "params": {}},
        },
    },
    {
        "rule_key": "seed-invalid-details-resend-link",
        "priority": 20,
        "definition": {
            "precondition": {
                "all": [
                    {"field": "failure_reason", "op": "eq", "value": "invalid_details"}
                ]
            },
            "action": {"name": RecoveryAction.SEND_PAYMENT_LINK.value, "params": {}},
        },
    },
]


def seed(repo: RuleRepository) -> None:
    """Insert the seed rules as ACTIVE, version 1."""
    for entry in SEED_RULES:
        repo.add_rule(
            entry["rule_key"],
            entry["definition"],
            status=RuleStatus.ACTIVE,
            priority=entry["priority"],
        )
