"""Persistence for the guardrail layer.

Defines the :class:`GuardStore` protocol and two implementations that must
behave identically (a parity test in ``tests/`` runs the guardrail suite
against both):

* :class:`InMemoryGuardStore` — fast, for tests and ephemeral runs;
* :class:`SqlGuardStore` — durable (SQLite by default, Postgres-ready), which is
  what makes idempotency and the audit log *persisted* and replay-safe across
  process restarts.

The audit log is append-only *by construction*: the protocol exposes only
``append_audit`` and ``read_audit`` — there is no update or delete.
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from sqlalchemy import (
    Boolean,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from agentcore.guardrails.model import (
    ApprovalRecord,
    ApprovalState,
    AuditEntry,
    IdempotencyRecord,
)


def _new_approval_id() -> str:
    return "apr_" + uuid.uuid4().hex


@runtime_checkable
class GuardStore(Protocol):
    """Storage contract the executor depends on.

    Note the audit surface: append and read only. Callers (and subclasses)
    cannot mutate or delete an existing audit entry through this interface.
    """

    # --- idempotency (terminal, executed effects only) ---
    def get_result(self, run_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        ...

    def put_result(
        self, run_id: str, idempotency_key: str, status: str, result_json: str
    ) -> None:
        ...

    # --- approvals ---
    def get_approval(self, run_id: str, idempotency_key: str) -> ApprovalRecord | None:
        ...

    def get_approval_by_id(self, approval_id: str) -> ApprovalRecord | None:
        ...

    def create_pending(
        self,
        run_id: str,
        idempotency_key: str,
        action_name: str,
        subject_id: str,
        cost: float,
        params_json: str,
    ) -> ApprovalRecord:
        ...

    def set_approval_state(self, approval_id: str, state: ApprovalState) -> None:
        ...

    def list_pending(self, run_id: str) -> list[ApprovalRecord]:
        ...

    # --- budget / spend / breaker (per run) ---
    def subject_executed_count(self, run_id: str, subject_id: str) -> int:
        ...

    def accumulated_spend(self, run_id: str) -> float:
        ...

    def record_execution(self, run_id: str, subject_id: str, cost: float) -> None:
        """Atomically bump the subject's executed count and the run spend."""
        ...

    def is_breaker_open(self, run_id: str) -> bool:
        ...

    def trip_breaker(self, run_id: str, reason: str) -> None:
        ...

    # --- audit (append-only) ---
    def append_audit(
        self,
        run_id: str,
        timestamp: datetime,
        action_name: str,
        subject_id: str,
        idempotency_key: str,
        cost: float,
        decision: str,
        detail: str,
    ) -> AuditEntry:
        ...

    def read_audit(self, run_id: str) -> list[AuditEntry]:
        ...


class InMemoryGuardStore:
    """Thread-safe in-memory implementation of :class:`GuardStore`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._idem: dict[tuple[str, str], IdempotencyRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._approval_by_key: dict[tuple[str, str], str] = {}
        self._exec_counts: dict[tuple[str, str], int] = {}
        self._spend: dict[str, float] = {}
        self._breaker: dict[str, bool] = {}
        self._audit: list[AuditEntry] = []
        self._seq: dict[str, int] = {}

    def get_result(self, run_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        with self._lock:
            return self._idem.get((run_id, idempotency_key))

    def put_result(
        self, run_id: str, idempotency_key: str, status: str, result_json: str
    ) -> None:
        with self._lock:
            self._idem[(run_id, idempotency_key)] = IdempotencyRecord(
                run_id=run_id,
                idempotency_key=idempotency_key,
                status=status,
                result_json=result_json,
                created_at=datetime.now(timezone.utc),
            )

    def get_approval(self, run_id: str, idempotency_key: str) -> ApprovalRecord | None:
        with self._lock:
            approval_id = self._approval_by_key.get((run_id, idempotency_key))
            return self._approvals.get(approval_id) if approval_id else None

    def get_approval_by_id(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def create_pending(
        self,
        run_id: str,
        idempotency_key: str,
        action_name: str,
        subject_id: str,
        cost: float,
        params_json: str,
    ) -> ApprovalRecord:
        with self._lock:
            existing = self._approval_by_key.get((run_id, idempotency_key))
            if existing:
                return self._approvals[existing]
            record = ApprovalRecord(
                approval_id=_new_approval_id(),
                run_id=run_id,
                idempotency_key=idempotency_key,
                action_name=action_name,
                subject_id=subject_id,
                cost=cost,
                params_json=params_json,
                state=ApprovalState.PENDING,
                created_at=datetime.now(timezone.utc),
            )
            self._approvals[record.approval_id] = record
            self._approval_by_key[(run_id, idempotency_key)] = record.approval_id
            return record

    def set_approval_state(self, approval_id: str, state: ApprovalState) -> None:
        with self._lock:
            record = self._approvals[approval_id]
            record.state = state
            record.decided_at = datetime.now(timezone.utc)

    def list_pending(self, run_id: str) -> list[ApprovalRecord]:
        with self._lock:
            return [
                r
                for r in self._approvals.values()
                if r.run_id == run_id and r.state is ApprovalState.PENDING
            ]

    def subject_executed_count(self, run_id: str, subject_id: str) -> int:
        with self._lock:
            return self._exec_counts.get((run_id, subject_id), 0)

    def accumulated_spend(self, run_id: str) -> float:
        with self._lock:
            return self._spend.get(run_id, 0.0)

    def record_execution(self, run_id: str, subject_id: str, cost: float) -> None:
        with self._lock:
            self._exec_counts[(run_id, subject_id)] = (
                self._exec_counts.get((run_id, subject_id), 0) + 1
            )
            self._spend[run_id] = self._spend.get(run_id, 0.0) + cost

    def is_breaker_open(self, run_id: str) -> bool:
        with self._lock:
            return self._breaker.get(run_id, False)

    def trip_breaker(self, run_id: str, reason: str) -> None:
        with self._lock:
            self._breaker[run_id] = True

    def append_audit(
        self,
        run_id: str,
        timestamp: datetime,
        action_name: str,
        subject_id: str,
        idempotency_key: str,
        cost: float,
        decision: str,
        detail: str,
    ) -> AuditEntry:
        with self._lock:
            seq = self._seq.get(run_id, 0) + 1
            self._seq[run_id] = seq
            entry = AuditEntry(
                run_id=run_id,
                seq=seq,
                timestamp=timestamp,
                action_name=action_name,
                subject_id=subject_id,
                idempotency_key=idempotency_key,
                cost=cost,
                decision=decision,
                detail=detail,
            )
            self._audit.append(entry)
            return entry

    def read_audit(self, run_id: str) -> list[AuditEntry]:
        with self._lock:
            return sorted(
                (e for e in self._audit if e.run_id == run_id), key=lambda e: e.seq
            )


# --- SQLAlchemy implementation -------------------------------------------

# A private declarative base so the guardrail tables are self-contained and
# never entangled with any other layer's metadata (agentcore stays neutral).
class _Base(DeclarativeBase):
    pass


class _Idem(_Base):
    __tablename__ = "guardrail_idempotency"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(64))


class _Approval(_Base):
    __tablename__ = "guardrail_approvals"
    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)
    action_name: Mapped[str] = mapped_column(String(128))
    subject_id: Mapped[str] = mapped_column(String(255))
    cost: Mapped[float] = mapped_column(Float)
    params_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class _RunState(_Base):
    __tablename__ = "guardrail_runstate"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    accumulated_spend: Mapped[float] = mapped_column(Float, default=0.0)
    breaker_open: Mapped[bool] = mapped_column(Boolean, default=False)
    breaker_reason: Mapped[str] = mapped_column(String(255), default="")


class _SubjectCount(_Base):
    __tablename__ = "guardrail_subject_count"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    executed_count: Mapped[int] = mapped_column(Integer, default=0)


class _Audit(_Base):
    __tablename__ = "guardrail_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[str] = mapped_column(String(64))
    action_name: Mapped[str] = mapped_column(String(128))
    subject_id: Mapped[str] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    cost: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqlGuardStore:
    """Durable :class:`GuardStore` backed by SQLAlchemy.

    A single writer is assumed (the eval and the recovery app are
    single-process for money-moving work); SQLite's transactions provide the
    atomicity the executor relies on. Scaling this is discussed in the README's
    "what breaks at 10,000 tpm" section.
    """

    def __init__(self, database_url: str = "sqlite:///./guardrails.db") -> None:
        connect_args = (
            {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        )
        self._engine = create_engine(
            database_url, connect_args=connect_args, future=True
        )
        _Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False, future=True
        )
        self._lock = threading.RLock()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _get_or_create_runstate(self, session: Session, run_id: str) -> _RunState:
        row = session.get(_RunState, run_id)
        if row is None:
            row = _RunState(
                run_id=run_id,
                accumulated_spend=0.0,
                breaker_open=False,
                breaker_reason="",
            )
            session.add(row)
            session.flush()
        return row

    def get_result(self, run_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        with self._session() as session:
            row = session.get(_Idem, (run_id, idempotency_key))
            if row is None:
                return None
            return IdempotencyRecord(
                run_id=row.run_id,
                idempotency_key=row.idempotency_key,
                status=row.status,
                result_json=row.result_json,
                created_at=_parse(row.created_at),  # type: ignore[arg-type]
            )

    def put_result(
        self, run_id: str, idempotency_key: str, status: str, result_json: str
    ) -> None:
        with self._session() as session:
            row = session.get(_Idem, (run_id, idempotency_key))
            if row is None:
                session.add(
                    _Idem(
                        run_id=run_id,
                        idempotency_key=idempotency_key,
                        status=status,
                        result_json=result_json,
                        created_at=_iso(datetime.now(timezone.utc)),
                    )
                )

    def _to_approval(self, row: _Approval) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row.approval_id,
            run_id=row.run_id,
            idempotency_key=row.idempotency_key,
            action_name=row.action_name,
            subject_id=row.subject_id,
            cost=row.cost,
            params_json=row.params_json,
            state=ApprovalState(row.state),
            created_at=_parse(row.created_at),  # type: ignore[arg-type]
            decided_at=_parse(row.decided_at),
        )

    def get_approval(self, run_id: str, idempotency_key: str) -> ApprovalRecord | None:
        with self._session() as session:
            row = session.scalars(
                select(_Approval).where(
                    _Approval.run_id == run_id,
                    _Approval.idempotency_key == idempotency_key,
                )
            ).first()
            return self._to_approval(row) if row else None

    def get_approval_by_id(self, approval_id: str) -> ApprovalRecord | None:
        with self._session() as session:
            row = session.get(_Approval, approval_id)
            return self._to_approval(row) if row else None

    def create_pending(
        self,
        run_id: str,
        idempotency_key: str,
        action_name: str,
        subject_id: str,
        cost: float,
        params_json: str,
    ) -> ApprovalRecord:
        with self._lock, self._session() as session:
            existing = session.scalars(
                select(_Approval).where(
                    _Approval.run_id == run_id,
                    _Approval.idempotency_key == idempotency_key,
                )
            ).first()
            if existing:
                return self._to_approval(existing)
            row = _Approval(
                approval_id=_new_approval_id(),
                run_id=run_id,
                idempotency_key=idempotency_key,
                action_name=action_name,
                subject_id=subject_id,
                cost=cost,
                params_json=params_json,
                state=ApprovalState.PENDING.value,
                created_at=_iso(datetime.now(timezone.utc)),
                decided_at=None,
            )
            session.add(row)
            session.flush()
            return self._to_approval(row)

    def set_approval_state(self, approval_id: str, state: ApprovalState) -> None:
        with self._session() as session:
            row = session.get(_Approval, approval_id)
            if row is not None:
                row.state = state.value
                row.decided_at = _iso(datetime.now(timezone.utc))

    def list_pending(self, run_id: str) -> list[ApprovalRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(_Approval).where(
                    _Approval.run_id == run_id,
                    _Approval.state == ApprovalState.PENDING.value,
                )
            ).all()
            return [self._to_approval(r) for r in rows]

    def subject_executed_count(self, run_id: str, subject_id: str) -> int:
        with self._session() as session:
            row = session.get(_SubjectCount, (run_id, subject_id))
            return row.executed_count if row else 0

    def accumulated_spend(self, run_id: str) -> float:
        with self._session() as session:
            row = session.get(_RunState, run_id)
            return row.accumulated_spend if row else 0.0

    def record_execution(self, run_id: str, subject_id: str, cost: float) -> None:
        with self._lock, self._session() as session:
            runstate = self._get_or_create_runstate(session, run_id)
            runstate.accumulated_spend += cost
            count = session.get(_SubjectCount, (run_id, subject_id))
            if count is None:
                session.add(
                    _SubjectCount(
                        run_id=run_id, subject_id=subject_id, executed_count=1
                    )
                )
            else:
                count.executed_count += 1

    def is_breaker_open(self, run_id: str) -> bool:
        with self._session() as session:
            row = session.get(_RunState, run_id)
            return bool(row.breaker_open) if row else False

    def trip_breaker(self, run_id: str, reason: str) -> None:
        with self._lock, self._session() as session:
            runstate = self._get_or_create_runstate(session, run_id)
            runstate.breaker_open = True
            runstate.breaker_reason = reason

    def append_audit(
        self,
        run_id: str,
        timestamp: datetime,
        action_name: str,
        subject_id: str,
        idempotency_key: str,
        cost: float,
        decision: str,
        detail: str,
    ) -> AuditEntry:
        with self._lock, self._session() as session:
            max_seq = session.scalar(
                select(func.max(_Audit.seq)).where(_Audit.run_id == run_id)
            )
            seq = (max_seq or 0) + 1
            session.add(
                _Audit(
                    run_id=run_id,
                    seq=seq,
                    timestamp=_iso(timestamp),
                    action_name=action_name,
                    subject_id=subject_id,
                    idempotency_key=idempotency_key,
                    cost=cost,
                    decision=decision,
                    detail=detail,
                )
            )
            return AuditEntry(
                run_id=run_id,
                seq=seq,
                timestamp=timestamp,
                action_name=action_name,
                subject_id=subject_id,
                idempotency_key=idempotency_key,
                cost=cost,
                decision=decision,
                detail=detail,
            )

    def read_audit(self, run_id: str) -> list[AuditEntry]:
        with self._session() as session:
            rows = session.scalars(
                select(_Audit)
                .where(_Audit.run_id == run_id)
                .order_by(_Audit.seq)
            ).all()
            return [
                AuditEntry(
                    run_id=r.run_id,
                    seq=r.seq,
                    timestamp=_parse(r.timestamp),  # type: ignore[arg-type]
                    action_name=r.action_name,
                    subject_id=r.subject_id,
                    idempotency_key=r.idempotency_key,
                    cost=r.cost,
                    decision=r.decision,
                    detail=r.detail,
                )
                for r in rows
            ]
