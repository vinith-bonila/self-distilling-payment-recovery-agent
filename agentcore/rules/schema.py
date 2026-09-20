"""The grammar's vocabulary, injected by the caller.

A :class:`Schema` says which context fields exist and their types, and which
actions exist and their typed parameters. The grammar itself knows nothing
about payments: everything domain-specific lives in the schema a caller builds.

Field/param types are primitive on purpose — NUMBER, STRING, BOOL and ENUM
(a closed set of allowed string values). An ENUM's members are plain strings,
so the grammar never imports or handles a domain enum *class*; the caller maps
its own enums down to allowed string values.
"""
from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field


class FieldType(enum.Enum):
    NUMBER = "number"
    STRING = "string"
    BOOL = "bool"
    ENUM = "enum"


@dataclass(frozen=True)
class FieldSpec:
    """Type of a context field. ENUM fields must list their allowed values."""

    type: FieldType
    allowed_values: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.type is FieldType.ENUM and not self.allowed_values:
            raise ValueError("ENUM field requires a non-empty allowed_values set")


@dataclass(frozen=True)
class ParamSpec:
    """Type of an action parameter (same primitive types as fields)."""

    type: FieldType
    allowed_values: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.type is FieldType.ENUM and not self.allowed_values:
            raise ValueError("ENUM param requires a non-empty allowed_values set")


@dataclass(frozen=True)
class ActionSpec:
    """A permitted action: a name and a fixed set of typed parameters."""

    name: str
    params: Mapping[str, ParamSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class Schema:
    """The closed vocabulary a rule may use.

    ``fields`` are the only context fields a predicate may reference; ``actions``
    are the only actions a rule may take. ``max_predicates`` and ``max_in_list``
    bound the size of a proposal so a hostile input cannot be pathologically
    large.
    """

    fields: Mapping[str, FieldSpec]
    actions: Mapping[str, ActionSpec]
    max_predicates: int = 8
    max_in_list: int = 32
