"""Payment distiller: shadow, promotion, demotion, provenance, safety."""
from __future__ import annotations

from typing import Any

from agentcore.distill import ShadowStats
from agentcore.llm_client import LLMResponse
from config import Settings
from providers.fake import FakeProvider
from recovery.distiller import PaymentDistiller
from recovery.router import PolicyRouter, Route
from recovery.rule_schema import payment_rule_schema
from recovery.rules_repo import RuleRepository, RuleStatus
from llm.stub import StubLLMClient


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "approval_threshold_inr": 5000.0,
        "per_run_spend_cap_inr": 100000.0,
        "per_subject_action_budget": 5,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def _make(tmp_db, proposer=None):
    schema = payment_rule_schema()
    repo = RuleRepository(schema)
    distiller = PaymentDistiller(repo, schema, proposer or StubLLMClient(), _settings())
    return distiller, repo


def _ctx(reason: str, amount: float = 1000.0, failed: int = 0, hours: float = 5.0):
    return {
        "failure_reason": reason,
        "amount_inr": amount,
        "method": "card",
        "customer_successful_payments": 3,
        "customer_failed_payments": failed,
        "hours_since_last_attempt": hours,
        "attempt_number": 1,
    }


def _feed(distiller, ctx, action, recovered, n, from_agent=True):
    for i in range(n):
        distiller.observe(ctx, action, recovered, from_agent=from_agent, source_id=f"traj_{i}")


_REASON = "expired_card"
_KEY = "distilled:failure_reason=expired_card"


def test_candidate_starts_in_shadow(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    shadow_keys = {r.rule_key for r in repo.list_by_status(RuleStatus.SHADOW)}
    assert _KEY in shadow_keys
    assert _KEY not in {r.rule_key for r in repo.list_by_status(RuleStatus.ACTIVE)}


def test_shadow_rule_produces_no_provider_side_effects(tmp_db) -> None:
    # A shadow rule that matches a case must never route or cause an effect.
    provider = FakeProvider(webhook_secret="whsec_fake")
    calls = {"n": 0}
    for name in ("create_payment_link", "refund_payment"):
        original = getattr(provider, name)

        def spy(*a, _orig=original, **k):
            calls["n"] += 1
            return _orig(*a, **k)

        setattr(provider, name, spy)

    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    # Router uses ACTIVE rules only; a shadow match must escalate, not resolve.
    decision = PolicyRouter(repo).route(_ctx(_REASON))
    assert decision.route is Route.ESCALATE_TO_AGENT
    # Observing more matching cases (shadow evaluation) touches no provider.
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 10)
    assert calls["n"] == 0


def test_valid_candidate_promotes_and_executes_deterministically(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 35)  # shadow stats
    distiller.promote()
    active = {r.rule_key for r in repo.list_by_status(RuleStatus.ACTIVE)}
    assert _KEY in active
    decision = PolicyRouter(repo).route(_ctx(_REASON))
    assert decision.route is Route.DETERMINISTIC
    assert decision.action == "send_payment_link"
    assert decision.rule_key == _KEY


def test_insufficient_sample_cannot_promote(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 10)  # <30 shadow
    distiller.promote()
    assert _KEY not in {r.rule_key for r in repo.list_by_status(RuleStatus.ACTIVE)}


def test_insufficient_agreement_cannot_promote(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    # Half the subsequent traffic disagrees with the rule's action.
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    _feed(distiller, _ctx(_REASON), "escalate_to_human", True, 20)
    distiller.promote()
    assert _KEY not in {r.rule_key for r in repo.list_by_status(RuleStatus.ACTIVE)}


def test_degraded_outcome_cannot_promote(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    _feed(distiller, _ctx(_REASON), "send_payment_link", False, 40)  # recovery ~0
    distiller.promote()
    assert _KEY not in {r.rule_key for r in repo.list_by_status(RuleStatus.ACTIVE)}


def test_degraded_active_rule_auto_demotes_with_history(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 35)
    distiller.promote()
    assert _KEY in {r.rule_key for r in repo.list_by_status(RuleStatus.ACTIVE)}
    # Recent recovery collapses -> auto-demote.
    _feed(distiller, _ctx(_REASON), "send_payment_link", False, 30)
    distiller.demote()
    loaded = repo.get(_KEY, 1)
    assert loaded is not None and loaded.status is RuleStatus.DEMOTED
    assert len(loaded.provenance["demotion_history"]) == 1
    assert loaded.provenance["demoted_at"] is not None
    # A demoted rule no longer executes.
    assert PolicyRouter(repo).route(_ctx(_REASON)).route is Route.ESCALATE_TO_AGENT


def test_high_value_refund_rule_never_auto_promotes(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    definition = {
        "precondition": {"all": [{"field": "failure_reason", "op": "eq", "value": "unknown"}]},
        "action": {"name": "refund", "params": {"full": True}},
    }
    repo.add_rule(
        "distilled:refund-unbounded", definition, status=RuleStatus.SHADOW,
        provenance={"origin": "distilled", "demotion_history": []}, priority=50,
    )
    # Give it flawless shadow stats — it must STILL not promote.
    distiller._shadow["distilled:refund-unbounded"] = ShadowStats(100, 100, 100)
    distiller.promote()
    loaded = repo.get("distilled:refund-unbounded", 1)
    assert loaded is not None and loaded.status is RuleStatus.SHADOW
    assert "distilled:refund-unbounded" in distiller.log.blocked_high_value_refund


def test_refund_bounded_below_threshold_may_promote(tmp_db) -> None:
    # A refund bounded strictly below the approval threshold is not "high value".
    distiller, repo = _make(tmp_db)
    definition = {
        "precondition": {
            "all": [
                {"field": "failure_reason", "op": "eq", "value": "unknown"},
                {"field": "amount_inr", "op": "lt", "value": 5000},
            ]
        },
        "action": {"name": "refund", "params": {"full": True}},
    }
    repo.add_rule(
        "distilled:refund-bounded", definition, status=RuleStatus.SHADOW,
        provenance={"origin": "distilled", "demotion_history": []}, priority=50,
    )
    distiller._shadow["distilled:refund-bounded"] = ShadowStats(100, 100, 100)
    distiller.promote()
    loaded = repo.get("distilled:refund-bounded", 1)
    assert loaded is not None and loaded.status is RuleStatus.ACTIVE


class _HostileProposer:
    model = "hostile"

    def complete(self, messages, *, temperature: float = 0.0, max_tokens: int = 1024):
        return LLMResponse(
            text='{"precondition":{"all":[{"field":"__evil__","op":"eq","value":"x"}]},'
            '"action":{"name":"rm_rf","params":{}}}',
            prompt_tokens=1,
            completion_tokens=1,
        )


def test_hostile_proposal_is_rejected(tmp_db) -> None:
    distiller, repo = _make(tmp_db, proposer=_HostileProposer())
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    assert distiller.log.invalid_proposals >= 1
    assert repo.list_by_status(RuleStatus.SHADOW) == []


def test_provenance_survives_persistence(tmp_db) -> None:
    distiller, repo = _make(tmp_db)
    _feed(distiller, _ctx(_REASON), "send_payment_link", True, 20)
    distiller.distill()
    # Reload through a fresh repository on the same database.
    reloaded = RuleRepository(payment_rule_schema()).get(_KEY, 1)
    assert reloaded is not None
    prov = reloaded.provenance
    assert prov["origin"] == "distilled"
    assert prov["situation_key"]["failure_reason"] == _REASON
    assert prov["source_trajectory_ids"]  # non-empty
    assert prov["prompt_version"] == "rule_proposer.v1"
    assert prov["created_at"]
