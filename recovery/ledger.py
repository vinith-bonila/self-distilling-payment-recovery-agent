"""Read and write the outcome ledger.

The single place ledger rows are created or queried; the live pipeline writes
through it and the dashboard reads through it.
"""
from __future__ import annotations

from typing import Any

from recovery.db import session_scope
from recovery.models import LedgerEntry


class LedgerRepository:
    """Persistence for :class:`~recovery.models.LedgerEntry`."""

    def record(self, **fields: Any) -> int:
        with session_scope() as session:
            row = LedgerEntry(**fields)
            session.add(row)
            session.flush()
            return row.id

    def for_event(self, internal_event_id: int) -> LedgerEntry | None:
        with session_scope() as session:
            return (
                session.query(LedgerEntry)
                .filter_by(internal_event_id=internal_event_id)
                .one_or_none()
            )

    def processed_delivery(self, provider: str, event_id: str) -> LedgerEntry | None:
        """An earlier *successfully processed* ledger row for the same provider
        event, if any. A previous ``failed`` attempt does not count, so a
        redelivery can retry it (the executor's idempotency keys still prevent a
        repeated effect)."""
        if not event_id:
            return None
        with session_scope() as session:
            return (
                session.query(LedgerEntry)
                .filter_by(provider=provider, event_id=event_id)
                .filter(LedgerEntry.path.notin_(("duplicate_delivery", "failed")))
                .order_by(LedgerEntry.id)
                .first()
            )

    def recent(self, limit: int = 25) -> list[LedgerEntry]:
        with session_scope() as session:
            return (
                session.query(LedgerEntry)
                .order_by(LedgerEntry.id.desc())
                .limit(limit)
                .all()
            )

    def pending_approvals(self) -> list[LedgerEntry]:
        with session_scope() as session:
            return (
                session.query(LedgerEntry)
                .filter_by(outcome="pending_approval")
                .order_by(LedgerEntry.id)
                .all()
            )

    def count(self) -> int:
        with session_scope() as session:
            return session.query(LedgerEntry).count()
