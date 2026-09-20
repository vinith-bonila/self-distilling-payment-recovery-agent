# Evaluation results — pre-distillation baseline

> **Synthetic.** These numbers come from a synthetic generator with a hidden ground-truth model and a frozen customer simulator. They demonstrate the *mechanism*; they are not real production outcomes.

> **Modelled cost.** Cost figures are MODELLED = token accounting × a configured price, not real spend.


- Cases: **500** (seed 42)
- Treated: 447 · Control (holdout): 53
- Routed to LLM: 405 · Resolved by deterministic rules: 42
- Concept drift at case index: 250
- Retry budget used for the run: 3

## Recovery — raw vs incremental vs control

| Metric | Value |
| --- | --- |
| Raw recovery (treated) | 74.9% (95% CI 70.7%–78.7%, n=447) |
| Control recovery (natural, no action) | 13.2% (95% CI 6.5%–24.8%, n=53) |
| **Incremental recovery vs control** | **61.7%** |

Treated and control 95% intervals **do not overlap** (treated recovery is distinguishable from natural).

## Traffic, quality, cost

| Metric | Value |
| --- | --- |
| LLM share of treated traffic | 90.6% (95% CI 87.5%–93.0%, n=447) |
| Wrong-tool rate (vs hidden ground truth) | 12.5% (95% CI 9.8%–15.9%, n=447) |
| Modelled cost per 1,000 failures | $1.4971 (token accounting × configured price) |
| Latency p50 / p95 (modelled) | 2461 ms / 2472 ms |
| Invalid rule proposals rejected | 0 |
| Guardrail trips by type | {'(none)': 0} |

## Retry-budget analysis

What the customer simulator implies about how many retry attempts to allow.

| Retry budget | Recovery (treated) | Optimal |
| --- | --- | --- |
| 1 | 55.3% (95% CI 50.6%–59.8%, n=447) |  |
| 2 | 69.4% (95% CI 64.9%–73.4%, n=447) |  |
| 3 | 74.9% (95% CI 70.7%–78.7%, n=447) |  |
| 4 | 76.5% (95% CI 72.4%–80.2%, n=447) | ◀ optimal |
| 5 | 76.7% (95% CI 72.6%–80.4%, n=447) |  |
| 6 | 76.7% (95% CI 72.6%–80.4%, n=447) |  |
| 7 | 76.7% (95% CI 72.6%–80.4%, n=447) |  |
| 8 | 76.7% (95% CI 72.6%–80.4%, n=447) |  |

**Empirically discovered optimal retry budget: 4.**

## Interpretation

This is the baseline *before* distillation. LLM share is high because only two seed rules resolve traffic deterministically. Phase 7 should drive LLM share down while incremental recovery holds within overlapping confidence bands; a rule for the drifting cluster should demote at the drift point. If the curve does not move, that will be reported here.
