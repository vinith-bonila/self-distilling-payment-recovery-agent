"""Binomial-rate statistics for the eval harness.

Wilson score intervals are used everywhere a rate is reported, so "held flat" is
judged by overlapping intervals rather than by comparing point estimates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Return the Wilson score confidence interval for ``successes``/``n``.

    ``z`` defaults to 1.96 (95%). Returns (0, 0) for an empty sample.
    """
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass(frozen=True)
class RateEstimate:
    """A binomial rate with its Wilson interval."""

    successes: int
    n: int
    z: float = 1.96

    @property
    def rate(self) -> float:
        return self.successes / self.n if self.n else 0.0

    @property
    def ci(self) -> tuple[float, float]:
        return wilson_interval(self.successes, self.n, self.z)

    @property
    def ci_low(self) -> float:
        return self.ci[0]

    @property
    def ci_high(self) -> float:
        return self.ci[1]


def intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """True iff two closed intervals overlap (used to judge 'held flat')."""
    return a[0] <= b[1] and b[0] <= a[1]


def percentile(values: list[float], p: float) -> float:
    """Return the ``p``-th percentile (0..100) via nearest-rank on sorted data."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    rank = math.ceil(p / 100.0 * len(ordered))
    return ordered[min(rank, len(ordered)) - 1]
