"""Rule storage: versioning, provenance, status transitions, load-time validation."""
from __future__ import annotations

import json

import pytest

from agentcore.rules import RuleValidationError
from recovery.db import session_scope
from recovery.models import RuleRow
from recovery.rules_repo import RuleStatus
from recovery.seed_rules import seed

_DEF = {
    "precondition": {"all": [{"field": "failure_reason", "op": "eq", "value": "card_declined"}]},
    "action": {"name": "send_payment_link", "params": {}},
}


def test_seed_rules_are_active_and_ordered(repo) -> None:
    seed(repo)
    active = repo.active_rules()
    assert [r.rule_key for r in active] == [
        "seed-risk-blocked-escalate",  # priority 10
        "seed-invalid-details-resend-link",  # priority 20
    ]
    assert all(r.status is RuleStatus.ACTIVE for r in active)


def test_seed_provenance_recorded(repo) -> None:
    seed(repo)
    loaded = repo.get("seed-risk-blocked-escalate", 1)
    assert loaded is not None
    assert loaded.provenance["origin"] == "seed"
    assert loaded.provenance["promoted_at"] is not None


def test_versioning_assigns_incrementing_versions(repo) -> None:
    assert repo.add_rule("k", _DEF) == 1
    assert repo.add_rule("k", _DEF) == 2


def test_invalid_rule_rejected_on_ingest(repo) -> None:
    with pytest.raises(RuleValidationError):
        repo.add_rule("bad", {"precondition": "evil", "action": {}})


def test_status_transition_records_demotion_history(repo) -> None:
    repo.add_rule(
        "distilled-1",
        _DEF,
        status=RuleStatus.SHADOW,
        provenance={
            "origin": "distilled",
            "trajectory_ids": ["t1", "t2"],
            "created_at": "2026-01-01T00:00:00+00:00",
            "promoted_at": None,
            "demoted_at": None,
            "demotion_history": [],
            "performance": {},
        },
    )
    repo.set_status("distilled-1", 1, RuleStatus.ACTIVE)
    repo.set_status("distilled-1", 1, RuleStatus.DEMOTED, reason="recovery degraded vs baseline")

    loaded = repo.get("distilled-1", 1)
    assert loaded is not None
    assert loaded.status is RuleStatus.DEMOTED
    assert loaded.provenance["demoted_at"] is not None
    assert loaded.provenance["demotion_history"] == [
        {"at": loaded.provenance["demoted_at"], "reason": "recovery degraded vs baseline"}
    ]
    # A demoted rule is not evaluated by the router.
    assert loaded.rule_key not in {r.rule_key for r in repo.active_rules()}


def test_tampered_row_is_skipped_on_load(repo) -> None:
    # Directly insert an ACTIVE row whose stored definition is not a valid rule.
    with session_scope() as session:
        session.add(
            RuleRow(
                rule_key="tampered",
                version=1,
                status=RuleStatus.ACTIVE.value,
                priority=1,
                definition_json=json.dumps({"precondition": "evil", "action": {}}),
                provenance_json="{}",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
    # Re-validation on load drops it rather than making it executable.
    assert repo.active_rules() == []
