"""Write evals/results.md and evals/cost_curve.png from an EvalResult."""
from __future__ import annotations

from pathlib import Path

from evals.harness import EvalResult, optimal_retry_budget


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _ci(estimate) -> str:
    lo, hi = estimate.ci
    return f"{_pct(estimate.rate)} (95% CI {_pct(lo)}–{_pct(hi)}, n={estimate.n})"


def write_results_md(result: EvalResult, path: str | Path) -> Path:
    s = result.summary
    optimal = optimal_retry_budget(result.retry_sweep)
    lines: list[str] = []
    lines.append("# Evaluation results — pre-distillation baseline\n")
    lines.append(
        "> **Synthetic.** These numbers come from a synthetic generator with a "
        "hidden ground-truth model and a frozen customer simulator. They "
        "demonstrate the *mechanism*; they are not real production outcomes.\n"
    )
    lines.append(
        "> **Modelled cost.** Cost figures are MODELLED = token accounting × a "
        "configured price, not real spend.\n"
    )
    lines.append(f"\n- Cases: **{s['n_total']}** (seed {result.seed})")
    lines.append(f"- Treated: {s['n_treated']} · Control (holdout): {s['n_control']}")
    lines.append(
        f"- Routed to LLM: {s['n_llm']} · Resolved by deterministic rules: {s['n_deterministic']}"
    )
    lines.append(f"- Concept drift at case index: {result.drift_index}")
    lines.append(f"- Retry budget used for the run: {result.retry_budget}\n")

    lines.append("## Recovery — raw vs incremental vs control\n")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Raw recovery (treated) | {_ci(s['raw_recovery'])} |")
    lines.append(f"| Control recovery (natural, no action) | {_ci(s['control_recovery'])} |")
    lines.append(
        f"| **Incremental recovery vs control** | **{_pct(s['incremental_recovery'])}** |"
    )
    overlap = s["recovery_ci_overlaps_control"]
    lines.append(
        f"\nTreated and control 95% intervals "
        f"{'**overlap** (recovery not distinguishable from natural at this stage)' if overlap else '**do not overlap** (treated recovery is distinguishable from natural)'}.\n"
    )

    lines.append("## Traffic, quality, cost\n")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| LLM share of treated traffic | {_ci(s['llm_share'])} |")
    lines.append(f"| Wrong-tool rate (vs hidden ground truth) | {_ci(s['wrong_tool_rate'])} |")
    lines.append(
        f"| Modelled cost per 1,000 failures | ${s['modelled_cost_per_1000_usd']:.4f} "
        f"(token accounting × configured price) |"
    )
    lines.append(f"| Latency p50 / p95 (modelled) | {s['p50_latency_ms']:.0f} ms / {s['p95_latency_ms']:.0f} ms |")
    lines.append(f"| Invalid rule proposals rejected | {s['invalid_rule_proposals']} |")
    trips = s["guardrail_trips"] or {"(none)": 0}
    lines.append(f"| Guardrail trips by type | {trips} |\n")

    lines.append("## Retry-budget analysis\n")
    lines.append("What the customer simulator implies about how many retry attempts to allow.\n")
    lines.append("| Retry budget | Recovery (treated) | Optimal |")
    lines.append("| --- | --- | --- |")
    for row in result.retry_sweep:
        mark = "◀ optimal" if row.get("optimal") else ""
        lines.append(f"| {row['budget']} | {_ci(row['recovery'])} | {mark} |")
    lines.append(f"\n**Empirically discovered optimal retry budget: {optimal}.**\n")

    lines.append("## Interpretation\n")
    lines.append(
        "This is the baseline *before* distillation. LLM share is high because "
        "only two seed rules resolve traffic deterministically. Phase 7 should "
        "drive LLM share down while incremental recovery holds within overlapping "
        "confidence bands; a rule for the drifting cluster should demote at the "
        "drift point. If the curve does not move, that will be reported here.\n"
    )
    text = "\n".join(lines)
    out = Path(path)
    out.write_text(text, encoding="utf-8")
    return out


def write_cost_curve_png(result: EvalResult, path: str | Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = result.series
    x = [row["center_index"] for row in series]
    cost = [row["modelled_cost_per_1000_usd"] for row in series]
    recovery = [row["recovery_rate"] * 100 for row in series]
    ci_low = [row["recovery_ci"][0] * 100 for row in series]
    ci_high = [row["recovery_ci"][1] * 100 for row in series]
    llm_share = [row["llm_share"] * 100 for row in series]

    fig, axes = plt.subplots(3, 1, figsize=(8, 9), sharex=True)
    fig.suptitle("Baseline over simulated time (pre-distillation)")

    axes[0].plot(x, cost, marker="o", color="#c026d3")
    axes[0].set_ylabel("modelled cost /1k\n(tokens × price, $)")

    axes[1].plot(x, recovery, marker="o", color="#2563eb", label="recovery rate")
    axes[1].fill_between(x, ci_low, ci_high, color="#2563eb", alpha=0.15, label="95% Wilson CI")
    axes[1].set_ylabel("recovery rate (%)")
    axes[1].legend(loc="best", fontsize=8)

    axes[2].plot(x, llm_share, marker="o", color="#ea580c")
    axes[2].set_ylabel("LLM share (%)")
    axes[2].set_xlabel("simulated time (case index)")

    for ax in axes:
        ax.axvline(result.drift_index, color="#6b7280", linestyle="--", linewidth=1)
        ax.grid(True, alpha=0.2)
    axes[0].annotate(
        "concept drift",
        xy=(result.drift_index, max(cost) if cost else 0),
        xytext=(6, -6),
        textcoords="offset points",
        fontsize=8,
        color="#6b7280",
    )

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = Path(path)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out
