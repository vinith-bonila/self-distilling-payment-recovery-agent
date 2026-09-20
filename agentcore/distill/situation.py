"""Generic situation representation for clustering.

A ``Situation`` is a normalised, hashable set of (feature, value) string pairs.
The caller decides what the features are (a payment caller might use failure
reason, amount band, channel, ...); this module only groups and projects them.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Situation:
    """An ordered, hashable tuple of (feature, value) string pairs."""

    features: tuple[tuple[str, str], ...]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "Situation":
        return cls(tuple(sorted((str(k), str(v)) for k, v in mapping.items())))

    def project(self, feature_names: list[str]) -> "Situation":
        """Return a situation restricted to ``feature_names``."""
        wanted = set(feature_names)
        return Situation(tuple((k, v) for k, v in self.features if k in wanted))

    def as_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.features}


@dataclass(frozen=True)
class Observation:
    """One handled case: its situation, the action taken, and whether it
    recovered. ``source_id`` identifies the originating trajectory."""

    situation: Situation
    action: str
    recovered: bool
    source_id: str
