"""ORM models for the recovery layer.

Phase 1 defines only the raw webhook event store. The outcome ledger, rules
table, approval queue and trajectory persistence arrive in later phases.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
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
