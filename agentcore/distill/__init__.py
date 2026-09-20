"""Domain-neutral distillation mechanics.

Turns observed agent behaviour into candidate deterministic rules and manages
their SHADOW -> ACTIVE -> DEMOTED lifecycle with statistically principled
thresholds. It knows nothing about payments: it operates on generic
:class:`Situation` feature tuples, a generic action string, and outcome booleans.
The caller (``recovery``) injects the feature extraction, the grammar schema,
the proposer, and the rule store.
"""
from __future__ import annotations

from agentcore.distill.cluster import Cluster, cluster_observations
from agentcore.distill.lifecycle import (
    DemotionPolicy,
    PromotionPolicy,
    ShadowStats,
    qualifies_for_promotion,
    should_demote,
)
from agentcore.distill.situation import Observation, Situation

__all__ = [
    "Cluster",
    "DemotionPolicy",
    "Observation",
    "PromotionPolicy",
    "ShadowStats",
    "Situation",
    "cluster_observations",
    "qualifies_for_promotion",
    "should_demote",
]
