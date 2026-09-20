"""Database engine, session management and the declarative base.

SQLite by default; Postgres-ready by swapping ``DATABASE_URL``. Nothing here is
domain-specific beyond living in the recovery layer.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def make_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine.

    SQLite connections get ``check_same_thread=False`` so the FastAPI app and
    tests can share a connection across threads.
    """
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(database_url: str | None = None) -> None:
    """Create the engine, the session factory and all tables. Idempotent."""
    global _engine, _SessionLocal
    from recovery import models  # noqa: F401  # register ORM tables on Base

    _engine = make_engine(database_url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session scope; commits on success, rolls back on error."""
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None  # for type-checkers
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
