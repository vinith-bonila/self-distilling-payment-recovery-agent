"""LLM client implementations (infrastructure, not agent machinery).

These implement :class:`agentcore.llm_client.LLMClient`:

* a deterministic stub for offline runs (Phase 5),
* a disk-caching wrapper keyed on prompt hash (Phase 5),
* the real Groq client (Phase 5).

The dependency arrow is ``llm -> agentcore``; ``agentcore`` never imports this
package.
"""
