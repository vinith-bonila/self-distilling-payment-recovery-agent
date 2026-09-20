"""The baseline harness runs offline, deterministically, and reports sane metrics."""
from __future__ import annotations

from evals.harness import optimal_retry_budget, run_eval
from evals.report import write_cost_curve_png, write_results_md


def _run(tmp_path, n=150):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = f"sqlite:///{(tmp_path / 'rules.db').as_posix()}"
    return run_eval(n=n, seed=7, database_url=db)


def test_harness_runs_and_partitions_traffic(tmp_path) -> None:
    result = _run(tmp_path)
    s = result.summary
    assert s["n_total"] == 150
    assert s["n_treated"] + s["n_control"] == 150
    assert s["n_control"] > 0  # control group exists
    assert s["n_llm"] + s["n_deterministic"] == s["n_treated"]
    assert s["n_llm"] > 0  # some traffic reaches the LLM
    assert s["n_deterministic"] > 0  # seed rules resolve some deterministically


def test_metrics_have_confidence_intervals(tmp_path) -> None:
    s = _run(tmp_path).summary
    raw = s["raw_recovery"]
    assert raw.ci_low <= raw.rate <= raw.ci_high
    assert 0.0 <= s["llm_share"].rate <= 1.0
    assert 0.0 <= s["wrong_tool_rate"].rate <= 1.0
    assert s["modelled_cost_per_1000_usd"] >= 0.0
    assert s["invalid_rule_proposals"] == 0


def test_incremental_recovery_is_reported_against_control(tmp_path) -> None:
    s = _run(tmp_path).summary
    expected = s["raw_recovery"].rate - s["control_recovery"].rate
    assert abs(s["incremental_recovery"] - expected) < 1e-9
    assert isinstance(s["recovery_ci_overlaps_control"], bool)


def test_retry_sweep_monotonic_and_has_optimal(tmp_path) -> None:
    result = _run(tmp_path)
    rates = [row["recovery"].rate for row in result.retry_sweep]
    assert all(rates[i] <= rates[i + 1] + 1e-9 for i in range(len(rates) - 1))
    assert 1 <= optimal_retry_budget(result.retry_sweep) <= 8


def test_run_is_deterministic(tmp_path) -> None:
    a = _run(tmp_path / "a").summary
    b = _run(tmp_path / "b").summary
    assert a["raw_recovery"].successes == b["raw_recovery"].successes
    assert a["n_llm"] == b["n_llm"]


def test_report_files_are_written(tmp_path) -> None:
    result = _run(tmp_path)
    md = write_results_md(result, tmp_path / "results.md")
    png = write_cost_curve_png(result, tmp_path / "cost_curve.png")
    assert md.exists() and md.stat().st_size > 0
    assert png.exists() and png.stat().st_size > 0
    text = md.read_text(encoding="utf-8")
    assert "Modelled cost" in text
    assert "Incremental recovery" in text
    assert "Synthetic" in text
