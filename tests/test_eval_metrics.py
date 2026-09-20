"""Wilson intervals, overlap and percentiles."""
from __future__ import annotations

from agentcore.eval import RateEstimate, intervals_overlap, percentile, wilson_interval


def test_wilson_known_value() -> None:
    lo, hi = wilson_interval(50, 100)
    assert 0.40 < lo < 0.404
    assert 0.596 < hi < 0.60
    assert lo < 0.5 < hi


def test_wilson_empty_sample() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_rate_estimate() -> None:
    est = RateEstimate(3, 10)
    assert est.rate == 0.3
    assert est.ci_low <= 0.3 <= est.ci_high


def test_intervals_overlap() -> None:
    assert intervals_overlap((0.4, 0.6), (0.55, 0.7))
    assert not intervals_overlap((0.4, 0.5), (0.55, 0.7))


def test_percentile() -> None:
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile([], 50) == 0.0
