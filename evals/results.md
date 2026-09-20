# Evaluation results — baseline vs distillation

> **Synthetic.** Numbers come from a synthetic generator with a hidden ground-truth model and a frozen customer simulator. They demonstrate the *mechanism*, not real production outcomes.

> **Modelled cost.** Cost = MODELLED token accounting × a configured price, not real spend.


Same deterministic run for both columns: 500 cases, seed 42, drift at index 250. Only distillation differs; the generator, simulator, ground truth, seed and thresholds are identical.

## Side by side

| Metric | Baseline (Phase 6) | Distilled (Phase 7) |
| --- | --- | --- |
| Raw recovery (treated) | 74.9% (95% CI 70.7%–78.7%, n=447) | 74.9% (95% CI 70.7%–78.7%, n=447) |
| Control recovery (natural) | 13.2% (95% CI 6.5%–24.8%, n=53) | 13.2% (95% CI 6.5%–24.8%, n=53) |
| Incremental vs control | 61.7% | 61.7% |
| **LLM share of traffic** | 90.6% | **55.9%** |
| **Modelled cost / 1,000** | $1.4971 | **$0.9238** |
| Wrong-tool rate | 12.5% | 12.5% |
| Latency p50 / p95 (modelled) | 2461/2472 ms | 2454/2472 ms |
| Invalid rule proposals rejected | 0 | 0 |
| Guardrail trips | (none) | (none) |

### Held flat?

Baseline and distilled recovery 95% Wilson intervals **overlap — recovery held flat** by the predefined criterion, while LLM share fell from 90.6% to 55.9% and modelled cost from $1.4971 to $0.9238 per 1,000.

## Distillation activity

- Shadow candidates created: 6 — ['distilled:failure_reason=insufficient_funds', 'distilled:failure_reason=card_declined', 'distilled:failure_reason=expired_card', 'distilled:failure_reason=processing_error', 'distilled:failure_reason=authentication_required', 'distilled:failure_reason=unknown']
- Average shadow agreement with the agent: 1.000
- Promoted rules:
    - `distilled:failure_reason=insufficient_funds` (support 39, agreement 1.0, recovery 0.795)
    - `distilled:failure_reason=card_declined` (support 31, agreement 1.0, recovery 0.71)
    - `distilled:failure_reason=expired_card` (support 33, agreement 1.0, recovery 0.758)
- Demoted rules:
    - `distilled:failure_reason=card_declined` — recent recovery 9/30 upper bound below 0.5
- High-value refund rules blocked from auto-promotion: (none)

## Retry-budget analysis (distilled run)

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

## Honest reading

Distillation converted validated agent behaviour into deterministic rules that took over a large share of traffic with recovery held flat and modelled cost down. Not every reason promoted within the stream (lower-frequency reasons did not accumulate the required shadow support in 500 cases), so LLM share falls but does not reach zero. The card_declined rule demoted automatically at the concept-drift point, as designed. In this environment the hidden context-dependence flips the correct action for only a small fraction of cases, so reason-level rules suffice for most traffic and the wrong-tool rate is unchanged (rules replicate the agent, they do not out-think it).
