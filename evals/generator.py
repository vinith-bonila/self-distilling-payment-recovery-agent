"""Synthetic failed-payment generator.

Owns the ground-truth timing: it stamps each case with ``post_drift`` at a fixed
point in the stream. Draws are fully deterministic for a given seed. Emits a
realistic long-tail rupee distribution and a hidden persona per case. Only the
observable fields are ever exposed to the recovery system; persona and the
ground-truth functions are eval-only.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Reason mix (long-tail); the two context-dependent reasons together are ~40%.
_REASON_WEIGHTS = {
    "insufficient_funds": 0.30,  # context-dependent
    "card_declined": 0.22,  # drifts mid-stream
    "expired_card": 0.12,
    "authentication_required": 0.10,
    "processing_error": 0.10,  # context-dependent
    "invalid_details": 0.07,  # seed rule covers this
    "risk_blocked": 0.05,  # seed rule covers this
    "unknown": 0.04,
}
_METHOD_WEIGHTS = {"card": 0.5, "upi": 0.3, "netbanking": 0.12, "wallet": 0.08}

DRIFT_FRACTION = 0.5  # drift occurs halfway through the stream


@dataclass(frozen=True)
class Persona:
    """Hidden customer persona driving the simulator's response."""

    price_sensitivity: float
    patience: float
    channel_preference: str
    abandonment_threshold: int


@dataclass(frozen=True)
class SyntheticCase:
    """One synthetic failed payment. Only the non-persona, non-post_drift fields
    are observable to the recovery system."""

    index: int
    case_id: str
    payment_id: str
    customer_id: str
    order_id: str
    amount_inr: float
    failure_reason: str
    method: str
    customer_successful_payments: int
    customer_failed_payments: int
    hours_since_last_attempt: float
    attempt_number: int
    # eval-only:
    persona: Persona
    post_drift: bool


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    r = rng.random() * sum(weights.values())
    upto = 0.0
    for key, weight in weights.items():
        upto += weight
        if r <= upto:
            return key
    return next(iter(weights))


def _long_tail_amount(rng: random.Random) -> float:
    """Lognormal rupee amount with a long tail; clamped to a sane range."""
    amount = math.exp(rng.gauss(7.0, 1.0))
    return round(min(max(amount, 50.0), 200000.0), 2)


def generate_cases(n: int = 500, seed: int = 42) -> list[SyntheticCase]:
    """Return ``n`` deterministic synthetic cases in simulated time order."""
    rng = random.Random(seed)
    drift_index = int(n * DRIFT_FRACTION)
    cases: list[SyntheticCase] = []
    for index in range(n):
        case_id = f"case_{seed}_{index:05d}"
        reason = _weighted_choice(rng, _REASON_WEIGHTS)
        method = _weighted_choice(rng, _METHOD_WEIGHTS)
        amount = _long_tail_amount(rng)
        successful = rng.randint(0, 20)
        failed = rng.randint(0, 5)
        hours = round(rng.expovariate(1 / 12.0), 2)
        attempt = rng.randint(1, 4)
        persona = Persona(
            price_sensitivity=round(rng.random(), 3),
            patience=round(rng.random(), 3),
            channel_preference=method,
            abandonment_threshold=rng.randint(1, 5),
        )
        cases.append(
            SyntheticCase(
                index=index,
                case_id=case_id,
                payment_id=f"pay_{case_id}",
                customer_id=f"cust_{seed}_{index % max(1, n // 5):04d}",
                order_id=f"order_{case_id}",
                amount_inr=amount,
                failure_reason=reason,
                method=method,
                customer_successful_payments=successful,
                customer_failed_payments=failed,
                hours_since_last_attempt=hours,
                attempt_number=attempt,
                persona=persona,
                post_drift=index >= drift_index,
            )
        )
    return cases


def drift_index(n: int) -> int:
    return int(n * DRIFT_FRACTION)
