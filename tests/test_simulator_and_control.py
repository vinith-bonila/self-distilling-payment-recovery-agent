"""Frozen customer simulator and deterministic control group."""
from __future__ import annotations

from evals import ground_truth as gt
from evals.control import DEFAULT_CONTROL_SHARE, is_control
from evals.generator import generate_cases
from evals.simulator import DeterministicCustomerSimulator


def test_simulator_is_deterministic() -> None:
    case = generate_cases(20, seed=1)[0]
    a = DeterministicCustomerSimulator().recovered(case, gt.SEND_LINK, retry_budget=3)
    b = DeterministicCustomerSimulator().recovered(case, gt.SEND_LINK, retry_budget=3)
    assert a == b


def test_link_recovery_probability_non_decreasing_in_retry_budget() -> None:
    sim = DeterministicCustomerSimulator()
    case = generate_cases(50, seed=2)[7]
    probs = [sim.recovery_probability(case, gt.SEND_LINK, b) for b in range(1, 9)]
    assert all(probs[i] <= probs[i + 1] + 1e-9 for i in range(len(probs) - 1))


def test_escalation_probability_unaffected_by_retry_budget() -> None:
    sim = DeterministicCustomerSimulator()
    case = generate_cases(50, seed=3)[9]
    p1 = sim.recovery_probability(case, gt.ESCALATE, 1)
    p8 = sim.recovery_probability(case, gt.ESCALATE, 8)
    assert p1 == p8


def test_control_group_is_deterministic_and_about_ten_percent() -> None:
    cases = generate_cases(2000, seed=42)
    controls = [c for c in cases if is_control(c)]
    share = len(controls) / len(cases)
    assert abs(share - DEFAULT_CONTROL_SHARE) < 0.03
    # stable across calls
    assert all(is_control(c) == is_control(c) for c in cases[:50])
