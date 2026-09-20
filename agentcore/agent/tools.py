"""Tool registry, typed argument validation, and observation bounding.

Security properties enforced here:

* The LLM can only name a *registered* tool; anything else is refused.
* Arguments are validated against a typed schema before a tool runs; unknown
  args, missing required args and wrong types are all rejected.
* An effect tool has no direct handler — only a ``build_action`` that yields an
  ``Action`` for the guarded executor. There is therefore no in-registry path
  that performs a side effect without going through the executor.
* Observations returned to the LLM are bounded and sanitised.
"""
from __future__ import annotations

import enum
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentcore.guardrails import Action

_MAX_STRING = 500
_MAX_ITEMS = 50
_MAX_ARG_STRING = 256


class ToolArgError(ValueError):
    """Raised when LLM-supplied tool arguments fail validation."""


class ToolParamType(enum.Enum):
    STRING = "string"
    NUMBER = "number"
    BOOL = "bool"


@dataclass(frozen=True)
class ToolParam:
    type: ToolParamType
    required: bool = True


ReadHandler = Callable[[Mapping[str, Any]], Any]
ActionBuilder = Callable[[Mapping[str, Any]], Action]


@dataclass(frozen=True)
class ToolSpec:
    """A tool the agent may call.

    Exactly one of ``read_handler`` (read-only) or ``build_action`` (side
    effect) must be set. An effect tool's ``build_action`` returns an ``Action``
    the loop hands to the guarded executor; it never performs the effect itself.
    """

    name: str
    description: str
    params: Mapping[str, ToolParam] = field(default_factory=dict)
    read_handler: ReadHandler | None = None
    build_action: ActionBuilder | None = None

    def __post_init__(self) -> None:
        if (self.read_handler is None) == (self.build_action is None):
            raise ValueError(
                "ToolSpec must have exactly one of read_handler or build_action"
            )

    @property
    def is_effect(self) -> bool:
        return self.build_action is not None


class ToolRegistry:
    """The closed set of tools available to one agent run."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def add(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def catalog(self) -> list[dict[str, Any]]:
        """A description of every tool for the LLM (names, params, types)."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "effect": spec.is_effect,
                "params": {
                    pname: {"type": p.type.value, "required": p.required}
                    for pname, p in spec.params.items()
                },
            }
            for spec in sorted(self._tools.values(), key=lambda s: s.name)
        ]


def validate_args(spec: ToolSpec, raw: object) -> dict[str, Any]:
    """Validate ``raw`` args against ``spec``; return a typed dict or raise."""
    if not isinstance(raw, Mapping):
        raise ToolArgError("arguments must be an object")
    allowed = set(spec.params)
    extra = set(raw) - allowed
    if extra:
        raise ToolArgError(f"unexpected arguments: {sorted(extra)}")
    result: dict[str, Any] = {}
    for name, param in spec.params.items():
        if name not in raw:
            if param.required:
                raise ToolArgError(f"missing required argument: {name}")
            continue
        result[name] = _check_scalar(raw[name], param.type, name)
    return result


def _check_scalar(value: object, param_type: ToolParamType, name: str) -> Any:
    if param_type is ToolParamType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolArgError(f"argument {name} must be a number")
        return value
    if param_type is ToolParamType.STRING:
        if not isinstance(value, str):
            raise ToolArgError(f"argument {name} must be a string")
        if len(value) > _MAX_ARG_STRING:
            raise ToolArgError(f"argument {name} exceeds {_MAX_ARG_STRING} chars")
        return value
    if param_type is ToolParamType.BOOL:
        if not isinstance(value, bool):
            raise ToolArgError(f"argument {name} must be a bool")
        return value
    raise ToolArgError(f"unsupported parameter type for {name}")


def bound_observation(value: Any, *, _depth: int = 0) -> Any:
    """Return a bounded, JSON-safe copy of ``value`` for the LLM/trajectory.

    Caps string length and container size and coerces unknown types to short
    strings, so a tool result can never blow up the prompt or smuggle huge or
    binary content back to the model.
    """
    if _depth > 4:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + "…"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                break
            out[str(k)[:_MAX_ARG_STRING]] = bound_observation(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [
            bound_observation(v, _depth=_depth + 1) for v in list(value)[:_MAX_ITEMS]
        ]
    return str(value)[:_MAX_STRING]
