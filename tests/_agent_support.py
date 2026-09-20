"""Test doubles and helpers for the agent-loop tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agentcore.llm_client import LLMMessage, LLMResponse
from config import Settings
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
from recovery.tools import RecoveryCase


class ScriptedLLMClient:
    """Returns a fixed sequence of raw responses (repeats the last)."""

    model = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(
        self, messages: list[LLMMessage], *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> LLMResponse:
        index = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return LLMResponse(
            text=self._responses[index],
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1.0,
            model=self.model,
        )


class CountingLLMClient:
    """Counts calls; always returns the same text. For cache tests."""

    model = "counting"

    def __init__(self, text: str = "{}") -> None:
        self.calls = 0
        self._text = text

    def complete(
        self, messages: list[LLMMessage], *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=self._text, prompt_tokens=2, completion_tokens=3, latency_ms=5.0, model=self.model
        )


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "approval_threshold_inr": 5000.0,
        "per_run_spend_cap_inr": 100000.0,
        "per_subject_action_budget": 5,
        "agent_max_iterations": 5,
        "agent_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def provider_with_payment(
    payment_id: str = "pay_1",
    customer_id: str = "cust_1",
    amount: float = 2500.0,
    reason: FailureReason = FailureReason.INSUFFICIENT_FUNDS,
) -> FakeProvider:
    provider = FakeProvider(webhook_secret="whsec_fake")
    provider.add_payment(
        Payment(
            id=payment_id,
            amount_inr=amount,
            currency="INR",
            status=PaymentStatus.FAILED,
            method=PaymentMethod.CARD,
            order_id="order_1",
            customer_id=customer_id,
            failure_reason=reason,
            created_at=datetime.now(timezone.utc),
        )
    )
    provider.add_order(
        Order(id="order_1", amount_inr=amount, currency="INR", status=OrderStatus.ATTEMPTED)
    )
    provider.add_customer_history(
        CustomerHistory(
            customer_id=customer_id,
            total_payments=5,
            successful_payments=3,
            failed_payments=2,
        )
    )
    return provider


def case(
    payment_id: str = "pay_1",
    customer_id: str = "cust_1",
    amount: float = 2500.0,
    reason: str = "insufficient_funds",
    method: str = "card",
) -> RecoveryCase:
    return RecoveryCase(
        payment_id=payment_id,
        amount_inr=amount,
        failure_reason=reason,
        method=method,
        customer_id=customer_id,
        order_id="order_1",
    )


def case_view(c: RecoveryCase) -> dict[str, Any]:
    return {
        "payment_id": c.payment_id,
        "amount_inr": c.amount_inr,
        "failure_reason": c.failure_reason,
        "method": c.method,
        "customer_id": c.customer_id,
    }
