"""Baseline evaluation harness (pre-distillation).

Replays synthetic failed payments in simulated time order. Each non-control case
is routed: obvious reasons resolve deterministically (no LLM), the rest go to the
agent (stub LLM). Recovery is decided by the customer simulator, never assumed
from a tool call. All headline recovery is reported incremental to the shadow
control group, with Wilson intervals. Runs fully offline.

Costs and latency are MODELLED (token accounting x a configured price); the
labels say so wherever they appear.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from typing import Any

from agentcore.eval import RateEstimate, intervals_overlap, percentile
from config import Settings
from llm.stub import StubLLMClient
from providers.fake import FakeProvider
from providers.types import (
    CustomerHistory,
    FailureReason,
    Order,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from recovery.agent import run_recovery
from recovery.db import init_db
from recovery.router import PolicyRouter, Route
from recovery.rule_schema import payment_rule_schema
from recovery.rules_repo import RuleRepository
from recovery.seed_rules import seed as seed_rules
from recovery.tools import RecoveryCase

from evals import ground_truth as gt
from evals.control import is_control
from evals.generator import SyntheticCase, drift_index, generate_cases
from evals.simulator import DeterministicCustomerSimulator

# --- modelled cost / latency (clearly not real spend) --------------------
MODELLED_INPUT_USD_PER_1K = 0.00059
MODELLED_OUTPUT_USD_PER_1K = 0.00079
MODELLED_BASE_MS = 250.0
MODELLED_MS_PER_TOKEN = 0.8
DET_PATH_LATENCY_MS = 5.0

DEFAULT_RETRY_BUDGET = 3
RETRY_SWEEP_MAX = 8
SERIES_WINDOWS = 10


@dataclass
class Record:
    index: int
    case_id: str
    path: str  # control | deterministic | llm
    action: str | None
    recovered: bool
    cost_usd: float
    latency_ms: float
    iterations: int
    wrong_tool: bool | None
    rule_key: str | None
    rule_version: int | None
    trips: dict[str, int]
    post_drift: bool
    case: SyntheticCase  # in-memory only (not serialised)


@dataclass
class EvalResult:
    n: int
    seed: int
    retry_budget: int
    drift_index: int
    records: list[Record]
    summary: dict[str, Any]
    series: list[dict[str, Any]]
    retry_sweep: list[dict[str, Any]]
    distillation: dict[str, Any] | None = None


def _gt_features(case: SyntheticCase) -> gt.GroundTruthFeatures:
    return gt.GroundTruthFeatures(
        case_id=case.case_id,
        failure_reason=case.failure_reason,
        amount_inr=case.amount_inr,
        customer_failed_payments=case.customer_failed_payments,
        hours_since_last_attempt=case.hours_since_last_attempt,
        post_drift=case.post_drift,
    )


def _observable_context(case: SyntheticCase) -> dict[str, Any]:
    return {
        "failure_reason": case.failure_reason,
        "amount_inr": case.amount_inr,
        "method": case.method,
        "customer_successful_payments": case.customer_successful_payments,
        "customer_failed_payments": case.customer_failed_payments,
        "hours_since_last_attempt": case.hours_since_last_attempt,
        "attempt_number": case.attempt_number,
    }


def _seed_provider(case: SyntheticCase) -> FakeProvider:
    provider = FakeProvider(webhook_secret="whsec_fake")
    provider.add_payment(
        Payment(
            id=case.payment_id,
            amount_inr=case.amount_inr,
            currency="INR",
            status=PaymentStatus.FAILED,
            method=PaymentMethod(case.method),
            order_id=case.order_id,
            customer_id=case.customer_id,
            failure_reason=FailureReason(case.failure_reason),
        )
    )
    provider.add_order(
        Order(id=case.order_id, amount_inr=case.amount_inr, currency="INR", status=OrderStatus.ATTEMPTED)
    )
    provider.add_customer_history(
        CustomerHistory(
            customer_id=case.customer_id,
            total_payments=case.customer_successful_payments + case.customer_failed_payments,
            successful_payments=case.customer_successful_payments,
            failed_payments=case.customer_failed_payments,
        )
    )
    return provider


def _modelled_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens / 1000.0 * MODELLED_INPUT_USD_PER_1K
        + completion_tokens / 1000.0 * MODELLED_OUTPUT_USD_PER_1K
    )


def _trips(trajectory) -> dict[str, int]:
    out: dict[str, int] = {}
    for step in trajectory.steps:
        d = step.decision
        if d.startswith("effect_") and d not in {"effect_executed", "effect_duplicate"}:
            out[d] = out.get(d, 0) + 1
    return out


def _process(
    case: SyntheticCase,
    router: PolicyRouter,
    simulator: DeterministicCustomerSimulator,
    llm: StubLLMClient,
    settings: Settings,
    retry_budget: int,
) -> Record:
    if is_control(case):
        recovered = simulator.recovered(case, gt.NO_ACTION, retry_budget)
        return Record(
            index=case.index, case_id=case.case_id, path="control", action=None,
            recovered=recovered, cost_usd=0.0, latency_ms=0.0, iterations=0,
            wrong_tool=None, rule_key=None, rule_version=None, trips={},
            post_drift=case.post_drift, case=case,
        )

    decision = router.route(_observable_context(case))
    if decision.route is Route.DETERMINISTIC:
        action = decision.action
        path, cost, latency, iterations, trips = "deterministic", 0.0, DET_PATH_LATENCY_MS, 0, {}
        rule_key, rule_version = decision.rule_key, decision.rule_version
    else:
        provider = _seed_provider(case)
        rcase = RecoveryCase(
            payment_id=case.payment_id,
            amount_inr=case.amount_inr,
            failure_reason=case.failure_reason,
            method=case.method,
            customer_id=case.customer_id,
            order_id=case.order_id,
        )
        trajectory = run_recovery(rcase, provider, llm, settings)
        action = trajectory.resolution_action
        path = "llm"
        cost = _modelled_cost(trajectory.total_prompt_tokens, trajectory.total_completion_tokens)
        latency = MODELLED_BASE_MS + trajectory.total_tokens * MODELLED_MS_PER_TOKEN
        iterations = trajectory.iterations
        trips = _trips(trajectory)
        rule_key = rule_version = None

    correct = gt.correct_action(_gt_features(case))
    recovered = simulator.recovered(case, action or gt.NO_ACTION, retry_budget)
    return Record(
        index=case.index, case_id=case.case_id, path=path, action=action,
        recovered=recovered, cost_usd=cost, latency_ms=latency, iterations=iterations,
        wrong_tool=(action != correct), rule_key=rule_key, rule_version=rule_version,
        trips=trips, post_drift=case.post_drift, case=case,
    )


def run_eval(
    n: int = 500,
    seed: int = 42,
    retry_budget: int = DEFAULT_RETRY_BUDGET,
    *,
    database_url: str | None = None,
) -> EvalResult:
    """Run the baseline evaluation and compute all metrics. Fully offline."""
    cases = generate_cases(n, seed)

    db_url = database_url or f"sqlite:///{tempfile.mkdtemp()}/eval_rules.db"
    init_db(db_url)
    repo = RuleRepository(payment_rule_schema())
    if not repo.active_rules():
        seed_rules(repo)
    router = PolicyRouter(repo)
    simulator = DeterministicCustomerSimulator()
    llm = StubLLMClient()
    settings = Settings(_env_file=None)

    records = [_process(c, router, simulator, llm, settings, retry_budget) for c in cases]

    return EvalResult(
        n=n,
        seed=seed,
        retry_budget=retry_budget,
        drift_index=drift_index(n),
        records=records,
        summary=_summarize(records, simulator, retry_budget),
        series=_series(records, n),
        retry_sweep=_retry_sweep(records, simulator),
    )


def run_eval_distilled(
    n: int = 500,
    seed: int = 42,
    retry_budget: int = DEFAULT_RETRY_BUDGET,
    *,
    database_url: str | None = None,
) -> EvalResult:
    """Same deterministic eval as :func:`run_eval`, with streaming distillation.

    Rules distilled from successful agent trajectories enter SHADOW, are promoted
    when they meet the principled thresholds, and are demoted when their recent
    recovery degrades. Fully offline. Nothing about the generator, simulator,
    ground truth, seed or thresholds differs from the baseline.
    """
    from recovery.distiller import PaymentDistiller

    cases = generate_cases(n, seed)
    db_url = database_url or f"sqlite:///{tempfile.mkdtemp()}/eval_distill.db"
    init_db(db_url)
    schema = payment_rule_schema()
    repo = RuleRepository(schema)
    if not repo.active_rules():
        seed_rules(repo)
    router = PolicyRouter(repo)
    simulator = DeterministicCustomerSimulator()
    llm = StubLLMClient()
    settings = Settings(_env_file=None)
    distiller = PaymentDistiller(repo, schema, llm, settings)

    records: list[Record] = []
    for case in cases:
        record = _process(case, router, simulator, llm, settings, retry_budget)
        records.append(record)
        if record.path != "control":
            distiller.observe(
                _observable_context(case),
                record.action,
                record.recovered,
                from_agent=(record.path == "llm"),
                source_id=case.payment_id,
            )
        distiller.step(case.index + 1)

    summary = _summarize(records, simulator, retry_budget)
    summary["invalid_rule_proposals"] = distiller.log.invalid_proposals
    from recovery.rules_repo import RuleStatus

    final_rules = [
        {
            "rule_key": loaded.rule_key,
            "version": loaded.version,
            "status": loaded.status.value,
            "priority": loaded.priority,
            "definition": loaded.definition,
            "provenance": loaded.provenance,
        }
        for status in RuleStatus
        for loaded in repo.list_by_status(status)
    ]
    distillation = {
        "final_rules": final_rules,
        "shadow_created": distiller.log.shadow_created,
        "promoted": distiller.log.promoted,
        "demoted": distiller.log.demoted,
        "blocked_high_value_refund": distiller.log.blocked_high_value_refund,
        "invalid_proposals": distiller.log.invalid_proposals,
        "avg_shadow_agreement": distiller.average_shadow_agreement(),
    }
    return EvalResult(
        n=n, seed=seed, retry_budget=retry_budget, drift_index=drift_index(n),
        records=records, summary=summary, series=_series(records, n),
        retry_sweep=_retry_sweep(records, simulator), distillation=distillation,
    )


def _summarize(
    records: list[Record], simulator: DeterministicCustomerSimulator, retry_budget: int
) -> dict[str, Any]:
    treated = [r for r in records if r.path != "control"]
    control = [r for r in records if r.path == "control"]
    llm_cases = [r for r in treated if r.path == "llm"]

    raw = RateEstimate(sum(r.recovered for r in treated), len(treated))
    control_rate = RateEstimate(sum(r.recovered for r in control), len(control))
    llm_share = RateEstimate(len(llm_cases), len(treated))
    wrong_tool = RateEstimate(sum(1 for r in treated if r.wrong_tool), len(treated))

    total_cost = sum(r.cost_usd for r in treated)
    cost_per_1000 = (total_cost / len(treated) * 1000.0) if treated else 0.0
    latencies = [r.latency_ms for r in treated]

    trips: dict[str, int] = {}
    for r in records:
        for name, count in r.trips.items():
            trips[name] = trips.get(name, 0) + count

    return {
        "n_total": len(records),
        "n_treated": len(treated),
        "n_control": len(control),
        "n_llm": len(llm_cases),
        "n_deterministic": len(treated) - len(llm_cases),
        "raw_recovery": raw,
        "control_recovery": control_rate,
        "incremental_recovery": raw.rate - control_rate.rate,
        "recovery_ci_overlaps_control": intervals_overlap(raw.ci, control_rate.ci),
        "llm_share": llm_share,
        "wrong_tool_rate": wrong_tool,
        "modelled_cost_per_1000_usd": cost_per_1000,
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "guardrail_trips": trips,
        "invalid_rule_proposals": 0,  # no distillation in the baseline
    }


def _series(records: list[Record], n: int, windows: int = SERIES_WINDOWS) -> list[dict[str, Any]]:
    size = max(1, n // windows)
    out: list[dict[str, Any]] = []
    for w in range(windows):
        lo, hi = w * size, (w + 1) * size if w < windows - 1 else n
        treated = [r for r in records if lo <= r.index < hi and r.path != "control"]
        if not treated:
            continue
        recovery = RateEstimate(sum(r.recovered for r in treated), len(treated))
        llm_share = RateEstimate(sum(1 for r in treated if r.path == "llm"), len(treated))
        cost = sum(r.cost_usd for r in treated) / len(treated) * 1000.0
        out.append(
            {
                "window": w,
                "center_index": (lo + hi) // 2,
                "recovery_rate": recovery.rate,
                "recovery_ci": recovery.ci,
                "llm_share": llm_share.rate,
                "modelled_cost_per_1000_usd": cost,
            }
        )
    return out


def _retry_sweep(
    records: list[Record], simulator: DeterministicCustomerSimulator
) -> list[dict[str, Any]]:
    treated = [r for r in records if r.path != "control"]
    sweep: list[dict[str, Any]] = []
    best = 0.0
    for budget in range(1, RETRY_SWEEP_MAX + 1):
        recovered = sum(
            simulator.recovered(r.case, r.action or gt.NO_ACTION, budget) for r in treated
        )
        rate = RateEstimate(recovered, len(treated))
        best = max(best, rate.rate)
        sweep.append({"budget": budget, "recovery": rate})
    # Optimal = smallest budget within 1pp of the best observed recovery.
    optimal = next(
        (row["budget"] for row in sweep if row["recovery"].rate >= best - 0.01),
        RETRY_SWEEP_MAX,
    )
    for row in sweep:
        row["optimal"] = row["budget"] == optimal
    return sweep


def optimal_retry_budget(sweep: list[dict[str, Any]]) -> int:
    for row in sweep:
        if row.get("optimal"):
            return int(row["budget"])
    return sweep[-1]["budget"] if sweep else 1
