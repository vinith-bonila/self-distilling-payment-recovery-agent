"""Cluster observations by a projected situation key."""
from __future__ import annotations

from dataclasses import dataclass, field

from agentcore.distill.situation import Observation


@dataclass
class Cluster:
    """Observations sharing a projected situation key."""

    key: tuple[tuple[str, str], ...]
    support: int = 0
    action_counts: dict[str, int] = field(default_factory=dict)
    recovered_count: int = 0
    source_ids: list[str] = field(default_factory=list)

    @property
    def dominant_action(self) -> str:
        return max(self.action_counts, key=lambda a: self.action_counts[a])

    @property
    def action_agreement(self) -> float:
        """Fraction of the cluster that took the dominant action."""
        if self.support == 0:
            return 0.0
        return self.action_counts[self.dominant_action] / self.support

    @property
    def recovery_rate(self) -> float:
        return self.recovered_count / self.support if self.support else 0.0

    def key_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.key}


def cluster_observations(
    observations: list[Observation], key_features: list[str]
) -> list[Cluster]:
    """Group ``observations`` by their situation projected onto ``key_features``."""
    clusters: dict[tuple[tuple[str, str], ...], Cluster] = {}
    for obs in observations:
        key = obs.situation.project(key_features).features
        cluster = clusters.get(key)
        if cluster is None:
            cluster = Cluster(key=key)
            clusters[key] = cluster
        cluster.support += 1
        cluster.action_counts[obs.action] = cluster.action_counts.get(obs.action, 0) + 1
        cluster.recovered_count += int(obs.recovered)
        cluster.source_ids.append(obs.source_id)
    return list(clusters.values())
