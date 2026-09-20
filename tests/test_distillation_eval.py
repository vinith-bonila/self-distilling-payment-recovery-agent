"""End-to-end: distillation moves the curve while recovery holds flat."""
from __future__ import annotations

from agentcore.eval import intervals_overlap
from evals.harness import run_eval, run_eval_distilled


def _urls(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return (
        f"sqlite:///{(tmp_path / 'base.db').as_posix()}",
        f"sqlite:///{(tmp_path / 'dist.db').as_posix()}",
    )


def test_distillation_reduces_llm_share_and_cost_holding_recovery(tmp_path) -> None:
    base_url, dist_url = _urls(tmp_path)
    baseline = run_eval(500, 42, database_url=base_url)
    distilled = run_eval_distilled(500, 42, database_url=dist_url)
    b, d = baseline.summary, distilled.summary

    # LLM traffic share falls; modelled cost falls.
    assert d["llm_share"].rate < b["llm_share"].rate
    assert d["modelled_cost_per_1000_usd"] < b["modelled_cost_per_1000_usd"]
    # Recovery held flat by the predefined criterion (Wilson intervals overlap).
    assert intervals_overlap(b["raw_recovery"].ci, d["raw_recovery"].ci)


def test_distillation_promotes_and_demotes_at_drift(tmp_path) -> None:
    _, dist_url = _urls(tmp_path)
    distilled = run_eval_distilled(500, 42, database_url=dist_url)
    dd = distilled.distillation
    assert len(dd["promoted"]) >= 1
    demoted_keys = {r["rule_key"] for r in dd["demoted"]}
    # The drifting cluster (card_declined) must auto-demote.
    assert any("card_declined" in k for k in demoted_keys)
    assert dd["invalid_proposals"] == 0


def test_distilled_run_is_deterministic(tmp_path) -> None:
    a_base, a = _urls(tmp_path / "a")
    b_base, b = _urls(tmp_path / "b")
    da = run_eval_distilled(300, 42, database_url=a)
    db = run_eval_distilled(300, 42, database_url=b)
    assert da.summary["raw_recovery"].successes == db.summary["raw_recovery"].successes
    assert da.summary["llm_share"].successes == db.summary["llm_share"].successes
    assert [p["rule_key"] for p in da.distillation["promoted"]] == [
        p["rule_key"] for p in db.distillation["promoted"]
    ]
