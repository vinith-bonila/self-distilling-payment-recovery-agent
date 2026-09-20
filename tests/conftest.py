"""Shared fixtures for Phase 4+ tests that touch the recovery database."""
from __future__ import annotations

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Point the recovery DB at an isolated per-test SQLite file."""
    from recovery.db import init_db

    init_db(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    yield


@pytest.fixture
def payment_schema():
    from recovery.rule_schema import payment_rule_schema

    return payment_rule_schema()


@pytest.fixture
def repo(tmp_db, payment_schema):
    from recovery.rules_repo import RuleRepository

    return RuleRepository(payment_schema)
