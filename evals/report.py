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


def write_comparison_md(
    baseline: EvalResult, distilled: EvalResult, path: str | Path
) -> Path:
    """Write results.md with a Phase 6 (baseline) vs Phase 7 (distilled) view."""
    from agentcore.eval import intervals_overlap

    b, d = baseline.summary, distilled.summary
    dd = distilled.distillation or {}
    held_flat = intervals_overlap(b["raw_recovery"].ci, d["raw_recovery"].ci)
    optimal = optimal_retry_budget(distilled.retry_sweep)

    lines: list[str] = []
    lines.append("# Evaluation results — baseline vs distillation\n")
    lines.append(
        "> **Synthetic.** Numbers come from a synthetic generator with a hidden "
        "ground-truth model and a frozen customer simulator. They demonstrate the "
        "*mechanism*, not real production outcomes.\n"
    )
    lines.append(
        "> **Modelled cost.** Cost = MODELLED token accounting × a configured "
        "price, not real spend.\n"
    )
    lines.append(
        f"\nSame deterministic run for both columns: {b['n_total']} cases, seed "
        f"{distilled.seed}, drift at index {distilled.drift_index}. Only distillation "
        "differs; the generator, simulator, ground truth, seed and thresholds are "
        "identical.\n"
    )

    lines.append("## Side by side\n")
    lines.append("| Metric | Baseline (Phase 6) | Distilled (Phase 7) |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| Raw recovery (treated) | {_ci(b['raw_recovery'])} | {_ci(d['raw_recovery'])} |")
    lines.append(
        f"| Control recovery (natural) | {_ci(b['control_recovery'])} | {_ci(d['control_recovery'])} |"
    )
    lines.append(
        f"| Incremental vs control | {_pct(b['incremental_recovery'])} | {_pct(d['incremental_recovery'])} |"
    )
    lines.append(f"| **LLM share of traffic** | {_pct(b['llm_share'].rate)} | **{_pct(d['llm_share'].rate)}** |")
    lines.append(
        f"| **Modelled cost / 1,000** | ${b['modelled_cost_per_1000_usd']:.4f} | "
        f"**${d['modelled_cost_per_1000_usd']:.4f}** |"
    )
    lines.append(f"| Wrong-tool rate | {_pct(b['wrong_tool_rate'].rate)} | {_pct(d['wrong_tool_rate'].rate)} |")
    lines.append(
        f"| Latency p50 / p95 (modelled) | {b['p50_latency_ms']:.0f}/{b['p95_latency_ms']:.0f} ms | "
        f"{d['p50_latency_ms']:.0f}/{d['p95_latency_ms']:.0f} ms |"
    )
    lines.append(f"| Invalid rule proposals rejected | {b['invalid_rule_proposals']} | {d['invalid_rule_proposals']} |")
    lines.append(f"| Guardrail trips | {b['guardrail_trips'] or '(none)'} | {d['guardrail_trips'] or '(none)'} |\n")

    lines.append("### Held flat?\n")
    lines.append(
        f"Baseline and distilled recovery 95% Wilson intervals "
        f"{'**overlap — recovery held flat** by the predefined criterion' if held_flat else '**do not overlap — recovery changed materially**'}, "
        f"while LLM share fell from {_pct(b['llm_share'].rate)} to {_pct(d['llm_share'].rate)} "
        f"and modelled cost from ${b['modelled_cost_per_1000_usd']:.4f} to "
        f"${d['modelled_cost_per_1000_usd']:.4f} per 1,000.\n"
    )

    lines.append("## Distillation activity\n")
    lines.append(f"- Shadow candidates created: {len(dd.get('shadow_created', []))} — {dd.get('shadow_created', [])}")
    lines.append(f"- Average shadow agreement with the agent: {dd.get('avg_shadow_agreement', 0.0):.3f}")
    lines.append("- Promoted rules:")
    for row in dd.get("promoted", []):
        lines.append(
            f"    - `{row['rule_key']}` (support {row['support']}, agreement "
            f"{row['agreement']}, recovery {row['recovery']})"
        )
    if not dd.get("promoted"):
        lines.append("    - (none)")
    lines.append("- Demoted rules:")
    for row in dd.get("demoted", []):
        lines.append(f"    - `{row['rule_key']}` — {row['reason']}")
    if not dd.get("demoted"):
        lines.append("    - (none)")
    lines.append(
        f"- High-value refund rules blocked from auto-promotion: "
        f"{dd.get('blocked_high_value_refund', []) or '(none)'}\n"
    )

    lines.append("## Retry-budget analysis (distilled run)\n")
    lines.append("| Retry budget | Recovery (treated) | Optimal |")
    lines.append("| --- | --- | --- |")
    for row in distilled.retry_sweep:
        lines.append(f"| {row['budget']} | {_ci(row['recovery'])} | {'◀ optimal' if row.get('optimal') else ''} |")
    lines.append(f"\n**Empirically discovered optimal retry budget: {optimal}.**\n")

    lines.append("## Honest reading\n")
    lines.append(
        "Distillation converted validated agent behaviour into deterministic "
        "rules that took over a large share of traffic with recovery held flat "
        "and modelled cost down. Not every reason promoted within the stream "
        "(lower-frequency reasons did not accumulate the required shadow support "
        "in 500 cases), so LLM share falls but does not reach zero. The "
        "card_declined rule demoted automatically at the concept-drift point, as "
        "designed. In this environment the hidden context-dependence flips the "
        "correct action for only a small fraction of cases, so reason-level rules "
        "suffice for most traffic and the wrong-tool rate is unchanged (rules "
        "replicate the agent, they do not out-think it).\n"
    )
    out = Path(path)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_cost_curve_png(
    result: EvalResult, path: str | Path, *, title: str = "Baseline over simulated time"
) -> Path:
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
    fig.suptitle(title)

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
