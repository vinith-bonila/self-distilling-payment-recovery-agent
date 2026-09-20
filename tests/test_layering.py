"""Layering guard: ``agentcore`` must be domain- and vendor-neutral.

It must import nothing from ``providers``, ``recovery``, ``llm`` or ``evals``.
It defines the interfaces it needs and receives implementations by injection.
"""
from __future__ import annotations

from _ast_imports import REPO_ROOT, forbidden_hits

FORBIDDEN = {"providers", "recovery", "llm", "evals"}


def test_agentcore_imports_nothing_from_other_layers() -> None:
    offenders = forbidden_hits(REPO_ROOT / "agentcore", FORBIDDEN)
    assert not offenders, (
        "agentcore must not import from "
        f"{sorted(FORBIDDEN)}; violations: {offenders}"
    )
