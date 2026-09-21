"""Assemble and run the payment-recovery agent for one case.

Wires the domain-neutral :class:`~agentcore.agent.AgentLoop` to a provider, a
guarded executor (with policy from settings), the versioned prompt and an LLM
client. The guardrails (idempotency, approval, per-subject budget, spend cap)
are the executor's; nothing here re-implements them.
"""
from __future__ import annotations

from agentcore.agent import AgentLoop, ToolRegistry
from agentcore.guardrails import GuardedExecutor, GuardPolicy, GuardStore, InMemoryGuardStore
from agentcore.llm_client import LLMClient
from agentcore.trajectory import Trajectory
from config import Settings
from providers.base import PaymentProvider
from recovery.prompts import load_prompt
from recovery.tools import RecoveryCase, build_tools


def guard_policy(settings: Settings) -> GuardPolicy:
    """The single source of guardrail limits, for every path that acts."""
    return GuardPolicy(
        approval_threshold=settings.approval_threshold_inr,
        per_subject_action_budget=settings.per_subject_action_budget,
        per_run_spend_cap=settings.per_run_spend_cap_inr,
    )


def build_executor(
    case: RecoveryCase,
    provider: PaymentProvider,
    settings: Settings,
    *,
    store: GuardStore | None = None,
) -> tuple[GuardedExecutor, ToolRegistry]:
    """Build the guarded executor and tool registry for one case.

    Shared by the agent path and the deterministic-rule path, so both reach
    providers through the same executor, handlers and guardrails.
    """
    executor = GuardedExecutor(
        run_id=case.payment_id,
        store=store or InMemoryGuardStore(),
        policy=guard_policy(settings),
    )
    registry, handlers = build_tools(case, provider)
    for action_name, handler in handlers.items():
        executor.register(action_name, handler)
    return executor, registry


def build_agent(
    case: RecoveryCase,
    provider: PaymentProvider,
    llm: LLMClient,
    settings: Settings,
    *,
    store: GuardStore | None = None,
    prompt_version: str = "v1",
) -> AgentLoop:
    """Build the agent loop for ``case`` (its executor is exposed as ``loop.executor``)."""
    executor, registry = build_executor(case, provider, settings, store=store)

    prompt = load_prompt("agent_decider", prompt_version)
    return AgentLoop(
        llm=llm,
        registry=registry,
        executor=executor,
        system_prompt=prompt.text,
        system_prompt_id=prompt.id,
        max_iterations=settings.agent_max_iterations,
        timeout_seconds=settings.agent_timeout_seconds,
    )


def run_recovery(
    case: RecoveryCase,
    provider: PaymentProvider,
    llm: LLMClient,
    settings: Settings,
    *,
    store: GuardStore | None = None,
) -> Trajectory:
    """Run the agent for one case and return its trajectory."""
    loop = build_agent(case, provider, llm, settings, store=store)
    case_view = {
        "payment_id": case.payment_id,
        "amount_inr": case.amount_inr,
        "failure_reason": case.failure_reason,
        "method": case.method,
        "customer_id": case.customer_id,
    }
    return loop.run(subject_id=case.customer_id or case.payment_id, case=case_view)
