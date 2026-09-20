"""ORM models for the recovery layer.

Phase 1 defines only the raw webhook event store. The outcome ledger, rules
table, approval queue and trajectory persistence arrive in later phases.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from recovery.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawWebhookEvent(Base):
    """An inbound webhook persisted verbatim before any processing.

    Storing the raw body first means normalisation can be replayed and no event
    is ever lost to a downstream bug.
    """

    __tablename__ = "raw_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(255), index=True)
    event_type: Mapped[str] = mapped_column(String(128), default="")
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class InternalEvent(Base):
    """A webhook after signature check and normalisation.

    Holds normalised fields only (failure reason stored as the enum *value*
    string, never a provider string). The routing columns are populated later
    (Phase 5) when the agent/router processes the event; they stay null here.
    """

    __tablename__ = "internal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(255), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payment_id: Mapped[str] = mapped_column(String(255), index=True)
    amount_inr: Mapped[float] = mapped_column(Float)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    matched_rule_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    matched_rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class RuleRow(Base):
    """A versioned rule stored as DATA.

    ``definition_json`` is the validated proposal (precondition + action) as a
    plain JSON object — never code. ``provenance_json`` records origin,
    originating trajectories, promotion/demotion times and performance, so
    Phase 7 (distillation) can promote, demote and audit rules. Each
    (rule_key, version) is unique; a new version is a new row.
    """

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("rule_key", "version", name="uq_rule_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    definition_json: Mapped[str] = mapped_column(Text)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64))


class TrajectoryRow(Base):
    """A persisted agent trajectory (the full structured run as JSON)."""

    __tablename__ = "trajectories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(255), index=True)
    subject_id: Mapped[str] = mapped_column(String(255), index=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    resolution_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    system_prompt_id: Mapped[str] = mapped_column(String(64))
    iterations: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    total_latency_ms: Mapped[float] = mapped_column(Float)
    trajectory_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
