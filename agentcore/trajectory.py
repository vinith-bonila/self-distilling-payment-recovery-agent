"""Structured, replayable agent trajectories.

A trajectory captures everything about one agent run: each step's thought, the
tool and arguments chosen, the decision the loop made, the bounded observation
returned, and per-step token/latency/cache metadata — plus the run's outcome and
totals. It serialises to and from plain dicts so runs can be persisted, replayed
and clustered (Phase 7). Domain-neutral: nothing here mentions payments.
"""
from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class AgentOutcome(str, enum.Enum):
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    PENDING_APPROVAL = "pending_approval"
    MAX_ITERATIONS = "max_iterations"
    FAILED_TIMEOUT = "failed_timeout"
    FAILED_MALFORMED = "failed_malformed_output"
    REJECTED = "rejected"
    RUNNING = "running"


@dataclass
class TrajectoryStep:
    """One iteration of the agent loop."""

    index: int
    thought: str
    tool: str | None
    args: dict[str, Any] | None
    decision: str
    observation: Any
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrajectoryStep":
        return cls(**data)


@dataclass
class Trajectory:
    """The full record of one agent run."""

    run_id: str
    subject_id: str
    system_prompt_id: str
    started_at: str
    ended_at: str | None = None
    outcome: AgentOutcome = AgentOutcome.RUNNING
    resolution_action: str | None = None
    steps: list[TrajectoryStep] = field(default_factory=list)

    @property
    def iterations(self) -> int:
        return len(self.steps)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(s.prompt_tokens for s in self.steps)

    @property
    def total_completion_tokens(self) -> int:
        return sum(s.completion_tokens for s in self.steps)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def total_latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "subject_id": self.subject_id,
            "system_prompt_id": self.system_prompt_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome.value,
            "resolution_action": self.resolution_action,
            "iterations": self.iterations,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trajectory":
        return cls(
            run_id=data["run_id"],
            subject_id=data["subject_id"],
            system_prompt_id=data["system_prompt_id"],
            started_at=data["started_at"],
            ended_at=data.get("ended_at"),
            outcome=AgentOutcome(data["outcome"]),
            resolution_action=data.get("resolution_action"),
            steps=[TrajectoryStep.from_dict(s) for s in data.get("steps", [])],
        )
