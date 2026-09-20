"""Domain-neutral agent core.

Contains the tool registry, the hand-written agent loop, guardrails, the
trajectory log and replay, the distillation engine, and eval primitives.

Hard rule (enforced by ``tests/test_layering.py``): this package must never
import from ``providers``, ``recovery``, ``llm`` or ``evals``. It defines the
interfaces it needs (e.g. :class:`agentcore.llm_client.LLMClient`) and receives
concrete implementations by injection.
"""
