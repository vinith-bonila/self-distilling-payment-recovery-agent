"""Domain-neutral distillation mechanics: clustering and lifecycle thresholds."""
from __future__ import annotations

from agentcore.distill import (
    DemotionPolicy,
    Observation,
    PromotionPolicy,
    ShadowStats,
    Situation,
    cluster_observations,
    qualifies_for_promotion,
    should_demote,
)


def _obs(reason, band, action, recovered, source):
    return Observation(
        Situation.from_mapping({"failure_reason": reason, "amount_band": band}),
        action,
        recovered,
        source,
    )


def test_cluster_groups_by_projected_key() -> None:
    obs = [
        _obs("card_declined", "lt_2000", "send_payment_link", True, "t1"),
        _obs("card_declined", "gte_10000", "send_payment_link", True, "t2"),
        _obs("risk_blocked", "lt_2000", "escalate_to_human", True, "t3"),
    ]
    clusters = {c.key_dict()["failure_reason"]: c for c in cluster_observations(obs, ["failure_reason"])}
    assert clusters["card_declined"].support == 2
    assert clusters["card_declined"].dominant_action == "send_payment_link"
    assert clusters["card_declined"].action_agreement == 1.0
    assert clusters["card_declined"].recovery_rate == 1.0
    assert clusters["risk_blocked"].support == 1


def test_promotion_requires_support() -> None:
    stats = ShadowStats(matched=29, agreed=29, recovered=29)  # perfect but n<30
    assert not qualifies_for_promotion(stats, PromotionPolicy())


def test_promotion_requires_agreement() -> None:
    stats = ShadowStats(matched=100, agreed=70, recovered=100)  # 70% agreement
    assert not qualifies_for_promotion(stats, PromotionPolicy())


def test_promotion_requires_recovery_lower_bound() -> None:
    # High agreement but low recovery -> recovery Wilson LB below 0.5.
    stats = ShadowStats(matched=100, agreed=100, recovered=40)
    assert not qualifies_for_promotion(stats, PromotionPolicy())


def test_promotion_succeeds_when_all_criteria_met() -> None:
    stats = ShadowStats(matched=100, agreed=100, recovered=85)
    assert qualifies_for_promotion(stats, PromotionPolicy())


def test_demotion_requires_recent_sample() -> None:
    assert not should_demote(2, 10, DemotionPolicy())  # n<30


def test_demotion_fires_when_recovery_confidently_low() -> None:
    # 9/30 recovered -> Wilson upper bound well below 0.5.
    assert should_demote(9, 30, DemotionPolicy())


def test_demotion_does_not_fire_when_still_healthy() -> None:
    assert not should_demote(24, 30, DemotionPolicy())  # 80% recovery
