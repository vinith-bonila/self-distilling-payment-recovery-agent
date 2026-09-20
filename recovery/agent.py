"""Assemble and run the payment-recovery agent for one case.

Wires the domain-neutral :class:`~agentcore.agent.AgentLoop` to a provider, a
guarded executor (with policy from settings), the versioned prompt and an LLM
client. The guardrails (idempotency, approval, per-subject budget, spend cap)
are the executor's; nothing here re-implements them.
"""
from __future__ import annotations

from agentcore.agent import AgentLoop
from agentcore.guardrails import GuardedExecutor, GuardPolicy, GuardStore, InMemoryGuardStore
from agentcore.llm_client import LLMClient
from agentcore.trajectory import Trajectory
from config import Settings
from providers.base import PaymentProvider
from recovery.prompts import load_prompt
from recovery.tools import RecoveryCase, build_tools


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
    executor = GuardedExecutor(
        run_id=case.payment_id,
        store=store or InMemoryGuardStore(),
        policy=GuardPolicy(
            approval_threshold=settings.approval_threshold_inr,
            per_subject_action_budget=settings.per_subject_action_budget,
            per_run_spend_cap=settings.per_run_spend_cap_inr,
        ),
    )
    registry, handlers = build_tools(case, provider)
    for action_name, handler in handlers.items():
        executor.register(action_name, handler)

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
