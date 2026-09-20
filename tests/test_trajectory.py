"""Trajectory structure, serialisation round-trip, and persistence."""
from __future__ import annotations

import json

from _agent_support import case, provider_with_payment, settings

from agentcore.trajectory import Trajectory
from llm.stub import StubLLMClient
from recovery.agent import run_recovery


def test_trajectory_is_json_serialisable_and_round_trips() -> None:
    trajectory = run_recovery(case(), provider_with_payment(), StubLLMClient(), settings())
    data = trajectory.to_dict()
    json.dumps(data)  # must be serialisable
    restored = Trajectory.from_dict(data)
    assert restored.outcome == trajectory.outcome
    assert restored.iterations == trajectory.iterations
    assert [s.tool for s in restored.steps] == [s.tool for s in trajectory.steps]
    assert restored.total_tokens == trajectory.total_tokens
    assert restored.resolution_action == trajectory.resolution_action


def test_trajectory_records_metadata_per_step() -> None:
    trajectory = run_recovery(case(), provider_with_payment(), StubLLMClient(), settings())
    for step in trajectory.steps:
        assert step.index >= 1
        assert step.decision  # non-empty
        assert step.prompt_tokens >= 0
        assert step.completion_tokens >= 0
    assert trajectory.total_latency_ms >= 0.0


def test_trajectory_persistence_round_trip(tmp_db) -> None:
    from recovery.trajectory_store import TrajectoryStore

    trajectory = run_recovery(case(), provider_with_payment(), StubLLMClient(), settings())
    store = TrajectoryStore()
    row_id = store.save(trajectory)
    loaded = store.load(row_id)
    assert loaded is not None
    assert loaded.run_id == trajectory.run_id
    assert loaded.outcome == trajectory.outcome
    assert loaded.iterations == trajectory.iterations
