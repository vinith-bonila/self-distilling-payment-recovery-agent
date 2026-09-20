"""Domain-neutral evaluation primitives.

Statistics used by the eval harness: Wilson score intervals for binomial rates,
interval-overlap checks (for "held flat" claims), and percentiles. Nothing here
knows about payments; ``evals`` builds the domain-specific harness on top.
"""
from __future__ import annotations

from agentcore.eval.metrics import (
    RateEstimate,
    intervals_overlap,
    percentile,
    wilson_interval,
)

__all__ = ["RateEstimate", "intervals_overlap", "percentile", "wilson_interval"]
