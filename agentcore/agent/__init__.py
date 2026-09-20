"""Hand-written agent loop and tool registry (domain-neutral).

No agent framework. The loop drives an :class:`~agentcore.llm_client.LLMClient`
through a bounded number of iterations, lets it pick only from registered tools
with validated arguments, and routes every side effect through the Phase 2
:class:`~agentcore.guardrails.GuardedExecutor`. Effect tools carry no callable
of their own — only an ``Action`` builder — so there is no path to a side effect
that skips the executor.
"""
from __future__ import annotations

from agentcore.agent.loop import AgentLoop
from agentcore.agent.tools import (
    ToolArgError,
    ToolParam,
    ToolParamType,
    ToolRegistry,
    ToolSpec,
    bound_observation,
    validate_args,
)

__all__ = [
    "AgentLoop",
    "ToolArgError",
    "ToolParam",
    "ToolParamType",
    "ToolRegistry",
    "ToolSpec",
    "bound_observation",
    "validate_args",
]
