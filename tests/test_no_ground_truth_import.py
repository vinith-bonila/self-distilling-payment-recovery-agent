"""Eval-integrity guard: no production module imports the hidden ground truth.

The scenario generator's ground-truth outcome model lives in
``evals.ground_truth``. If any production layer imported it, the eval would be
circular. We forbid the entire ``evals`` package from every production layer,
which strictly subsumes forbidding ``evals.ground_truth`` alone.
"""
from __future__ import annotations

from _ast_imports import REPO_ROOT, forbidden_hits

PRODUCTION_PACKAGES = ["agentcore", "providers", "recovery", "llm"]


def test_no_production_module_imports_evals() -> None:
    all_offenders: dict[str, set[str]] = {}
    for package in PRODUCTION_PACKAGES:
        all_offenders.update(forbidden_hits(REPO_ROOT / package, {"evals"}))
    assert not all_offenders, (
        "Production code must not import from evals (esp. evals.ground_truth); "
        f"violations: {all_offenders}"
    )
