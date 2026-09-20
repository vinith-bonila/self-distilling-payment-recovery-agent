"""LLM client implementations (infrastructure, not agent machinery).

These implement :class:`agentcore.llm_client.LLMClient`:

* a deterministic stub for offline runs (Phase 5),
* a disk-caching wrapper keyed on prompt hash (Phase 5),
* the real Groq client (Phase 5).

The dependency arrow is ``llm -> agentcore``; ``agentcore`` never imports this
package.
"""
from __future__ import annotations

from llm.cache import CachingLLMClient
from llm.groq import GroqLLMClient
from llm.stub import StubLLMClient

__all__ = ["CachingLLMClient", "GroqLLMClient", "StubLLMClient"]
