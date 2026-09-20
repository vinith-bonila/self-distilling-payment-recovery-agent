"""Agent loop behaviour: happy path, cutoffs, and safe failure."""
from __future__ import annotations

from _agent_support import (
    ScriptedLLMClient,
    case,
    case_view,
    provider_with_payment,
    settings,
)

from agentcore.trajectory import AgentOutcome, Trajectory
from llm.stub import StubLLMClient
from providers.types import FailureReason
from recovery.agent import build_agent, run_recovery

_FETCH = '{"thought":"x","action":{"tool":"fetch_payment","args":{"payment_id":"pay_1"}}}'
_FINAL_NULL = '{"thought":"done","final":{"resolution":null}}'


def test_stub_happy_path_resolves_with_payment_link() -> None:
    trajectory = run_recovery(
        case(), provider_with_payment(), StubLLMClient(), settings()
    )
    assert trajectory.outcome is AgentOutcome.RESOLVED
    assert trajectory.resolution_action == "send_payment_link"
    tools = [s.tool for s in trajectory.steps]
    assert "fetch_payment" in tools
    assert "create_payment_link" in tools
    assert trajectory.iterations <= 5
    assert any(s.decision == "effect_executed" for s in trajectory.steps)


def test_stub_escalates_risk_blocked() -> None:
    provider = provider_with_payment(reason=FailureReason.RISK_BLOCKED)
    trajectory = run_recovery(
        case(reason="risk_blocked"), provider, StubLLMClient(), settings()
    )
    assert trajectory.outcome is AgentOutcome.ESCALATED
    assert trajectory.resolution_action == "escalate_to_human"


def test_max_iterations_cutoff() -> None:
    # The LLM only ever calls a read tool; it never finishes.
    loop = build_agent(
        case(), provider_with_payment(), ScriptedLLMClient([_FETCH]), settings(agent_max_iterations=5)
    )
    trajectory = loop.run(subject_id="cust_1", case=case_view(case()))
    assert trajectory.outcome is AgentOutcome.MAX_ITERATIONS
    assert trajectory.iterations == 5


def test_timeout_fails_safely() -> None:
    loop = build_agent(case(), provider_with_payment(), ScriptedLLMClient([_FETCH]), settings())
    times = iter([0, 10, 20, 30, 40, 50])
    loop._clock = lambda: next(times)  # inject deterministic clock; timeout 30s
    trajectory = loop.run(subject_id="cust_1", case=case_view(case()))
    assert trajectory.outcome is AgentOutcome.FAILED_TIMEOUT
    assert trajectory.iterations == 3  # steps at t=10,20,30; t=40 > 30 stops the loop


def test_malformed_output_fails_safely_and_is_replayable() -> None:
    loop = build_agent(case(), provider_with_payment(), ScriptedLLMClient(["not json {{"]), settings())
    trajectory = loop.run(subject_id="cust_1", case=case_view(case()))
    assert trajectory.outcome is AgentOutcome.FAILED_MALFORMED
    assert trajectory.iterations == 1
    assert trajectory.steps[0].decision == "malformed_output"
    # Fully replayable via serialisation round-trip.
    restored = Trajectory.from_dict(trajectory.to_dict())
    assert restored.outcome is AgentOutcome.FAILED_MALFORMED


def test_unknown_tool_rejected_then_run_continues() -> None:
    responses = [
        '{"thought":"x","action":{"tool":"delete_database","args":{}}}',
        _FINAL_NULL,
    ]
    loop = build_agent(case(), provider_with_payment(), ScriptedLLMClient(responses), settings())
    trajectory = loop.run(subject_id="cust_1", case=case_view(case()))
    assert trajectory.steps[0].decision == "rejected_unknown_tool"
    assert trajectory.steps[0].observation["error"] == "unknown_tool"
    assert not any(s.decision.startswith("effect_") for s in trajectory.steps)
    assert trajectory.outcome is AgentOutcome.RESOLVED


def test_invalid_arguments_rejected_then_run_continues() -> None:
    responses = [
        '{"thought":"x","action":{"tool":"fetch_payment","args":{"payment_id":123}}}',
        _FINAL_NULL,
    ]
    loop = build_agent(case(), provider_with_payment(), ScriptedLLMClient(responses), settings())
    trajectory = loop.run(subject_id="cust_1", case=case_view(case()))
    assert trajectory.steps[0].decision == "rejected_invalid_args"
    assert trajectory.steps[0].observation["error"] == "invalid_arguments"
