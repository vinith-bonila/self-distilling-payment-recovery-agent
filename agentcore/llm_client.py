"""The LLM interface the agent core depends on.

``agentcore`` depends only on this protocol, never on a concrete client. The
stub, disk-cache and Groq implementations live in the top-level ``llm``
package, so the dependency arrow is ``llm -> agentcore`` and never the reverse.
This keeps the agent core's dependency surface both domain- and vendor-neutral.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMMessage:
    """A single chat message handed to an LLM client."""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """The result of one completion, with accounting for the trajectory log."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    model: str = "unknown"
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        """Prompt plus completion tokens."""
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface the agent loop and distillation engine need."""

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Return a completion for ``messages``."""
        ...
