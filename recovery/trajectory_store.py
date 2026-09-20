"""Persist and load agent trajectories.

The full structured trajectory is stored as JSON (so it can be replayed and
clustered in Phase 7), alongside indexed summary columns.
"""
from __future__ import annotations

import json

from agentcore.trajectory import Trajectory
from recovery.db import session_scope
from recovery.models import TrajectoryRow


class TrajectoryStore:
    """SQL-backed storage for trajectories."""

    def save(self, trajectory: Trajectory) -> int:
        data = trajectory.to_dict()
        with session_scope() as session:
            row = TrajectoryRow(
                run_id=trajectory.run_id,
                subject_id=trajectory.subject_id,
                outcome=trajectory.outcome.value,
                resolution_action=trajectory.resolution_action,
                system_prompt_id=trajectory.system_prompt_id,
                iterations=trajectory.iterations,
                total_tokens=trajectory.total_tokens,
                total_latency_ms=trajectory.total_latency_ms,
                trajectory_json=json.dumps(data),
            )
            session.add(row)
            session.flush()
            return row.id

    def load(self, row_id: int) -> Trajectory | None:
        with session_scope() as session:
            row = session.get(TrajectoryRow, row_id)
            if row is None:
                return None
            return Trajectory.from_dict(json.loads(row.trajectory_json))

    def all_for_subject(self, subject_id: str) -> list[Trajectory]:
        with session_scope() as session:
            rows = (
                session.query(TrajectoryRow)
                .filter_by(subject_id=subject_id)
                .order_by(TrajectoryRow.id)
                .all()
            )
            return [Trajectory.from_dict(json.loads(r.trajectory_json)) for r in rows]
