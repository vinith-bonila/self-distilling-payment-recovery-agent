"""HIDDEN ground-truth outcome model for the scenario generator.

EVAL INTEGRITY — READ THIS
==========================
No module in ``agentcore``, ``providers``, ``recovery`` or ``llm`` may import
this module, ever. ``tests/test_no_ground_truth_import.py`` enforces that. The
recovery system must discover what works only from observable context and the
simulator's responses — never by reading this oracle.

What makes the eval non-circular lives here:

* **Context dependence.** For a subset of reasons the correct action is not a
  function of the reason alone; it depends on observable context (amount band,
  customer failure history, time since last attempt). A system that memorises
  reason -> action must plateau short of ceiling on these.
* **Label noise.** A small deterministic share of cases fail even when the
  statistically-correct action is taken.
* **Concept drift.** For one reason cluster the correct action flips at a point
  in the stream (``case.post_drift``). A rule promoted before the drift must
  degrade afterwards.

Only ``evals`` code (generator, simulator, harness) may import this.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

SEND_LINK = "send_payment_link"
ESCALATE = "escalate_to_human"
REFUND = "refund"
NO_ACTION = "no_action"

# Tunables of the *environment* (not tuned to results — fixed once).
CONTEXT_AMOUNT_BAND = 5000.0
CONTEXT_FAILED_HISTORY = 2
RECENT_ATTEMPT_HOURS = 1.0
NOISE_RATE = 0.08
NATURAL_RECOVERY_PROB = 0.12


@dataclass(frozen=True)
class GroundTruthFeatures:
    """The observable features the correct action may depend on, plus the
    drift flag stamped by the generator (the generator owns drift timing)."""

    case_id: str
    failure_reason: str
    amount_inr: float
    customer_failed_payments: int
    hours_since_last_attempt: float
    post_drift: bool


def _unit(case_id: str, salt: str) -> float:
    digest = hashlib.sha256(f"{case_id}:{salt}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def is_noisy(features: GroundTruthFeatures) -> bool:
    """Deterministic per-case label noise flag."""
    return _unit(features.case_id, "noise") < NOISE_RATE


def correct_action(features: GroundTruthFeatures) -> str:
    """The statistically-correct recovery action for a case.

    Reason-only clusters map straight to an action. Context-dependent clusters
    (insufficient_funds, processing_error) depend on observable context. The
    card_declined cluster drifts: link before the drift, escalation after.
    """
    reason = features.failure_reason

    if reason == "card_declined":
        return ESCALATE if features.post_drift else SEND_LINK

    if reason == "insufficient_funds":
        high_value = features.amount_inr > CONTEXT_AMOUNT_BAND
        repeat_failer = features.customer_failed_payments >= CONTEXT_FAILED_HISTORY
        return ESCALATE if (high_value and repeat_failer) else SEND_LINK

    if reason == "processing_error":
        return SEND_LINK if features.hours_since_last_attempt >= RECENT_ATTEMPT_HOURS else ESCALATE

    if reason in {"risk_blocked", "unknown"}:
        return ESCALATE

    # expired_card, authentication_required, invalid_details
    return SEND_LINK


def base_recovery_prob(features: GroundTruthFeatures, action: str) -> float:
    """The base probability an action recovers the payment (pre-persona).

    The customer simulator modulates this by persona and does the actual draw;
    recovery is never assumed from a successful tool call.
    """
    if action == NO_ACTION:
        return NATURAL_RECOVERY_PROB

    if action == correct_action(features):
        return 0.15 if is_noisy(features) else 0.85

    # Wrong action. Escalation still resolves a fair share (a human catches it);
    # a wrong link occasionally works; a refund almost never recovers a payment.
    if action == ESCALATE:
        return 0.45
    if action == SEND_LINK:
        return 0.30
    if action == REFUND:
        return 0.03
    return 0.10
