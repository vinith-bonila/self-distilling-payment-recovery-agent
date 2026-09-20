"""Customer simulators. Recovery is decided here, never assumed from a tool call.

The default is a FROZEN deterministic stub: a stated persona heuristic over the
ground-truth base probability, with a per-(case, action) deterministic draw. It
is written once and never tuned in response to eval results.

An optional ``CachedLLMCustomerSimulator`` enriches this with an LLM persona
judgement, cached on disk keyed on the prompt. It is NOT used by ``make demo``
(which stays offline); it exists for richer, reproducible reruns when a key or a
committed cache is available.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evals import ground_truth as gt
from evals.generator import SyntheticCase


def _unit(case_id: str, action: str) -> float:
    # Budget-independent on purpose: the retry budget changes the recovery
    # PROBABILITY, not the per-case coin, so recovery is monotonic in budget.
    key = f"{case_id}:{action}".encode("utf-8")
    return int(hashlib.sha256(key).hexdigest()[:8], 16) / 0xFFFFFFFF


def _features(case: SyntheticCase) -> gt.GroundTruthFeatures:
    return gt.GroundTruthFeatures(
        case_id=case.case_id,
        failure_reason=case.failure_reason,
        amount_inr=case.amount_inr,
        customer_failed_payments=case.customer_failed_payments,
        hours_since_last_attempt=case.hours_since_last_attempt,
        post_drift=case.post_drift,
    )


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class DeterministicCustomerSimulator:
    """Frozen persona heuristic + deterministic Bernoulli draw.

    Stated policy (frozen):

    * A payment link's single-attempt success rises with patience and falls with
      price sensitivity; multiple retries help, but only up to the persona's
      abandonment threshold (this is what the retry-budget analysis discovers).
    * Escalation to a human is barely affected by retries and slightly helped by
      patience.
    * Refund / no-action recovery is the ground-truth base, unmodulated.
    """

    def recovery_probability(
        self, case: SyntheticCase, action: str, retry_budget: int = 1
    ) -> float:
        base = gt.base_recovery_prob(_features(case), action)
        persona = case.persona
        if action == gt.SEND_LINK:
            single = base * (0.7 + 0.3 * persona.patience) * (1 - 0.3 * persona.price_sensitivity)
            single = _clamp(single)
            attempts = max(1, min(retry_budget, persona.abandonment_threshold))
            return _clamp(1.0 - (1.0 - single) ** attempts)
        if action == gt.ESCALATE:
            return _clamp(base * (0.85 + 0.15 * persona.patience))
        return _clamp(base)

    def recovered(
        self, case: SyntheticCase, action: str, retry_budget: int = 1
    ) -> bool:
        prob = self.recovery_probability(case, action, retry_budget)
        return _unit(case.case_id, action) < prob


class CachedLLMCustomerSimulator:
    """Optional LLM-driven simulator with a disk cache (not used by make demo).

    Falls back to the deterministic simulator on a cache miss when no client is
    configured, so it can never require the network unexpectedly.
    """

    def __init__(self, llm=None, cache_dir: str | Path = "evals/cache/simulator") -> None:
        self._llm = llm
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fallback = DeterministicCustomerSimulator()

    def _key(self, case: SyntheticCase, action: str, retry_budget: int) -> str:
        blob = json.dumps(
            {"case": case.case_id, "action": action, "budget": retry_budget},
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def recovered(self, case: SyntheticCase, action: str, retry_budget: int = 1) -> bool:
        path = self._dir / f"{self._key(case, action, retry_budget)}.json"
        if path.exists():
            return bool(json.loads(path.read_text(encoding="utf-8"))["recovered"])
        if self._llm is None:
            return self._fallback.recovered(case, action, retry_budget)
        # A real implementation would prompt self._llm with the persona here and
        # cache the decision. Kept out of the offline path deliberately.
        decision = self._fallback.recovered(case, action, retry_budget)
        path.write_text(json.dumps({"recovered": decision}), encoding="utf-8")
        return decision
