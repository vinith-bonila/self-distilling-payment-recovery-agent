"""Properties of the hidden ground-truth model (eval-only)."""
from __future__ import annotations

from evals import ground_truth as gt


def _f(reason, amount=1000.0, failed=0, hours=5.0, post_drift=False, case_id="c1"):
    return gt.GroundTruthFeatures(
        case_id=case_id,
        failure_reason=reason,
        amount_inr=amount,
        customer_failed_payments=failed,
        hours_since_last_attempt=hours,
        post_drift=post_drift,
    )


def test_reason_only_clusters() -> None:
    assert gt.correct_action(_f("risk_blocked")) == gt.ESCALATE
    assert gt.correct_action(_f("invalid_details")) == gt.SEND_LINK
    assert gt.correct_action(_f("expired_card")) == gt.SEND_LINK


def test_insufficient_funds_is_context_dependent() -> None:
    # High value + repeat failer -> escalate; otherwise link. Reason alone is
    # insufficient.
    assert gt.correct_action(_f("insufficient_funds", amount=9000, failed=3)) == gt.ESCALATE
    assert gt.correct_action(_f("insufficient_funds", amount=9000, failed=0)) == gt.SEND_LINK
    assert gt.correct_action(_f("insufficient_funds", amount=100, failed=3)) == gt.SEND_LINK


def test_processing_error_is_context_dependent() -> None:
    assert gt.correct_action(_f("processing_error", hours=5.0)) == gt.SEND_LINK
    assert gt.correct_action(_f("processing_error", hours=0.2)) == gt.ESCALATE


def test_card_declined_drifts() -> None:
    before = gt.correct_action(_f("card_declined", post_drift=False))
    after = gt.correct_action(_f("card_declined", post_drift=True))
    assert before == gt.SEND_LINK
    assert after == gt.ESCALATE
    assert before != after


def test_correct_action_beats_wrong_and_noise_is_deterministic() -> None:
    features = _f("expired_card")  # correct = SEND_LINK
    assert gt.base_recovery_prob(features, gt.SEND_LINK) > gt.base_recovery_prob(
        features, gt.REFUND
    )
    # noise flag is stable for a given case id
    assert gt.is_noisy(features) == gt.is_noisy(_f("expired_card"))


def test_noise_lowers_correct_action_probability() -> None:
    # Find a noisy and a non-noisy case id for the same reason.
    noisy = next(
        _f("expired_card", case_id=f"n{i}") for i in range(200) if gt.is_noisy(_f("expired_card", case_id=f"n{i}"))
    )
    clean = next(
        _f("expired_card", case_id=f"c{i}") for i in range(200) if not gt.is_noisy(_f("expired_card", case_id=f"c{i}"))
    )
    assert gt.base_recovery_prob(noisy, gt.SEND_LINK) < gt.base_recovery_prob(clean, gt.SEND_LINK)
