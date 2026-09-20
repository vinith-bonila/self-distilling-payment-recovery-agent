"""Promotion and demotion policies, with thresholds set from first principles.

These thresholds are fixed here BEFORE any comparison is run; they are not tuned
to make a result pass. Each is justified below.

Promotion (a SHADOW rule may become ACTIVE only if ALL hold):

* ``min_support = 30`` shadow observations. Rationale: the conventional minimum
  for the normal approximation to a binomial to be usable, and it bounds the
  95% Wilson half-width to roughly ±0.18 at p=0.5 — below this a rate estimate
  is too noisy to act on.
* ``min_agreement = 0.90`` point agreement with the validated agent's action,
  AND its Wilson lower bound ``>= 0.80``. Rationale: a rule that replaces the
  LLM on a situation must almost always choose what the agent chose there; the
  point threshold demands high agreement and the lower-bound condition refuses
  to promote on a lucky small sample.
* recovery Wilson lower bound ``>= 0.50`` on matched cases. Rationale: a
  distilled rule cements a fixed action, so it must recover at least a majority
  with confidence — we never promote a rule that would lock in a mostly-failing
  action. Because agreement is high, this recovery equals the agent's on those
  situations, so it is the outcome-parity / no-material-degradation check.

Demotion (an ACTIVE rule is demoted automatically when):

* ``min_recent = 30`` recent live firings have accumulated, AND
* the recovery Wilson UPPER bound over that recent window ``< 0.50``. Rationale:
  we are now confident (even optimistically) that the rule no longer recovers a
  majority — it has fallen below the bar that justified promotion. Using the
  upper bound (not the point) avoids demoting on noise, and the gap between the
  promotion floor (LB >= 0.50) and the demotion trigger (UB < 0.50) is a
  hysteresis band that prevents flapping.

z = 1.96 (95%) throughout.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentcore.eval import wilson_interval


@dataclass(frozen=True)
class PromotionPolicy:
    min_support: int = 30
    min_agreement: float = 0.90
    min_agreement_lb: float = 0.80
    min_recovery_lb: float = 0.50
    z: float = 1.96


@dataclass(frozen=True)
class DemotionPolicy:
    min_recent: int = 30
    recovery_ub_floor: float = 0.50
    z: float = 1.96


@dataclass
class ShadowStats:
    """Cumulative shadow observations for one candidate rule."""

    matched: int = 0
    agreed: int = 0
    recovered: int = 0

    def record(self, *, agreed: bool, recovered: bool) -> None:
        self.matched += 1
        self.agreed += int(agreed)
        self.recovered += int(recovered)

    @property
    def agreement_rate(self) -> float:
        return self.agreed / self.matched if self.matched else 0.0

    @property
    def recovery_rate(self) -> float:
        return self.recovered / self.matched if self.matched else 0.0


def qualifies_for_promotion(stats: ShadowStats, policy: PromotionPolicy) -> bool:
    """True iff ``stats`` satisfy every promotion criterion."""
    if stats.matched < policy.min_support:
        return False
    if stats.agreement_rate < policy.min_agreement:
        return False
    agreement_lb = wilson_interval(stats.agreed, stats.matched, policy.z)[0]
    if agreement_lb < policy.min_agreement_lb:
        return False
    recovery_lb = wilson_interval(stats.recovered, stats.matched, policy.z)[0]
    if recovery_lb < policy.min_recovery_lb:
        return False
    return True


def should_demote(
    recent_recovered: int, recent_total: int, policy: DemotionPolicy
) -> bool:
    """True iff a recent-window recovery is confidently below the majority floor."""
    if recent_total < policy.min_recent:
        return False
    recovery_ub = wilson_interval(recent_recovered, recent_total, policy.z)[1]
    return recovery_ub < policy.recovery_ub_floor
