"""Versioned, provenance-carrying rule storage.

Rules are DATA: each row holds a validated proposal (precondition + action) as
JSON plus provenance. This module validates a rule against the schema on ingest
*and* on load (so a tampered row cannot become executable), assigns versions,
and tracks status transitions with timestamps and demotion history — the
machinery Phase 7's distillation loop depends on.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agentcore.rules import Rule, RuleValidationError, Schema, parse_rule
from recovery.db import session_scope
from recovery.models import RuleRow


class RuleStatus(str, enum.Enum):
    """Lifecycle states. Seed rules start ACTIVE; distilled rules start SHADOW."""

    SHADOW = "shadow"
    ACTIVE = "active"
    DEMOTED = "demoted"
    DISABLED = "disabled"


@dataclass(frozen=True)
class LoadedRule:
    """A rule row parsed back into a typed rule plus its metadata."""

    rule_key: str
    version: int
    status: RuleStatus
    priority: int
    rule: Rule
    definition: dict[str, Any]
    provenance: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_provenance() -> dict[str, Any]:
    """Provenance for a hand-written seed rule."""
    return {
        "origin": "seed",
        "trajectory_ids": [],
        "created_at": _now_iso(),
        "promoted_at": _now_iso(),
        "demoted_at": None,
        "demotion_history": [],
        "performance": {},
    }


class RuleRepository:
    """CRUD + status transitions for versioned rules."""

    def __init__(self, schema: Schema) -> None:
        self._schema = schema

    def add_rule(
        self,
        rule_key: str,
        definition: dict[str, Any],
        *,
        status: RuleStatus = RuleStatus.ACTIVE,
        provenance: dict[str, Any] | None = None,
        priority: int = 100,
        version: int | None = None,
    ) -> int:
        """Validate and insert a rule; returns the assigned version.

        Raises :class:`RuleValidationError` if the definition fails the grammar,
        so an invalid rule can never be stored.
        """
        parse_rule(definition, self._schema)  # validate on ingest
        prov = provenance if provenance is not None else seed_provenance()
        with session_scope() as session:
            if version is None:
                versions = [
                    row.version
                    for row in session.query(RuleRow).filter_by(rule_key=rule_key)
                ]
                version = max(versions, default=0) + 1
            session.add(
                RuleRow(
                    rule_key=rule_key,
                    version=version,
                    status=status.value,
                    priority=priority,
                    definition_json=json.dumps(definition),
                    provenance_json=json.dumps(prov),
                    created_at=_now_iso(),
                )
            )
        return version

    def _load(self, status: RuleStatus) -> list[LoadedRule]:
        loaded: list[LoadedRule] = []
        with session_scope() as session:
            rows = (
                session.query(RuleRow)
                .filter_by(status=status.value)
                .order_by(RuleRow.priority)
                .all()
            )
            for row in rows:
                definition = json.loads(row.definition_json)
                try:
                    # Re-validate on load: a tampered row never becomes usable.
                    rule = parse_rule(definition, self._schema)
                except RuleValidationError:
                    continue
                loaded.append(
                    LoadedRule(
                        rule_key=row.rule_key,
                        version=row.version,
                        status=RuleStatus(row.status),
                        priority=row.priority,
                        rule=rule,
                        definition=definition,
                        provenance=json.loads(row.provenance_json),
                    )
                )
        return sorted(loaded, key=lambda item: item.priority)

    def active_rules(self) -> list[LoadedRule]:
        """Rules the router evaluates, in ascending priority order."""
        return self._load(RuleStatus.ACTIVE)

    def list_by_status(self, status: RuleStatus) -> list[LoadedRule]:
        return self._load(status)

    def get(self, rule_key: str, version: int) -> LoadedRule | None:
        with session_scope() as session:
            row = (
                session.query(RuleRow)
                .filter_by(rule_key=rule_key, version=version)
                .one_or_none()
            )
            if row is None:
                return None
            definition = json.loads(row.definition_json)
            rule = parse_rule(definition, self._schema)
            return LoadedRule(
                rule_key=row.rule_key,
                version=row.version,
                status=RuleStatus(row.status),
                priority=row.priority,
                rule=rule,
                definition=definition,
                provenance=json.loads(row.provenance_json),
            )

    def set_status(
        self,
        rule_key: str,
        version: int,
        status: RuleStatus,
        *,
        reason: str = "",
    ) -> None:
        """Transition a rule's status, recording promotion/demotion provenance."""
        with session_scope() as session:
            row = (
                session.query(RuleRow)
                .filter_by(rule_key=rule_key, version=version)
                .one()
            )
            row.status = status.value
            prov = json.loads(row.provenance_json)
            timestamp = _now_iso()
            if status is RuleStatus.ACTIVE:
                prov["promoted_at"] = timestamp
            if status is RuleStatus.DEMOTED:
                prov["demoted_at"] = timestamp
                prov.setdefault("demotion_history", []).append(
                    {"at": timestamp, "reason": reason}
                )
            row.provenance_json = json.dumps(prov)
