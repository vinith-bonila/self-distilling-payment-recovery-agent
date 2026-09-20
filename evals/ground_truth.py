"""HIDDEN ground-truth outcome model for the scenario generator.

EVAL INTEGRITY — READ THIS
==========================
No module in ``agentcore``, ``providers``, ``recovery`` or ``llm`` may import
this module, ever. ``tests/test_no_ground_truth_import.py`` enforces that.

The recovery system must never see the ground truth. If it could, the eval
would be circular: the system would be graded against a model it can read. The
correct action for a given failure must be *discoverable only through the
observable context and the customer simulator's responses*, exactly as it would
be in production.

Fleshed out in Phase 6 (hidden outcome model, concept drift, label noise).
"""
from __future__ import annotations

# Sentinel so the guard test has a concrete symbol to prove is never imported.
_GROUND_TRUTH_MARKER = "do-not-import-from-production"
