"""Deterministic offline stub LLM.

FROZEN POLICY — written once from a simple, stated heuristic and never revised
in response to eval results (see the project's stub/threshold discipline). If it
plateaus below ceiling, that is a finding to report, not a bug to tune.

The stated policy, in order:

1. If ``fetch_payment`` is available and not yet called, call it.
2. Else if ``fetch_customer_history`` is available, a customer id is known, and
   it has not been called, call it.
3. Else choose a single resolving action *from the failure reason alone*
   (deliberately context-blind — this is the weak reasoner distillation must
   offload): ``risk_blocked``/unknown -> escalate; everything else -> send a new
   payment link. The stub never issues refunds.
4. Once a resolving effect appears in history, finish.

It reads the structured JSON state the loop puts in the last user message, so it
is fully deterministic and needs no network.
"""
from __future__ import annotations

import json
from typing import Any

from agentcore.llm_client import LLMMessage, LLMResponse

_SEND_LINK_REASONS = {
    "insufficient_funds",
    "card_declined",
    "expired_card",
    "invalid_details",
    "authentication_required",
    "processing_error",
}


def _resolving_action(failure_reason: str, tool_names: set[str]) -> tuple[str, dict[str, Any]]:
    if failure_reason in _SEND_LINK_REASONS and "create_payment_link" in tool_names:
        return "create_payment_link", {}  # amount filled in by caller below
    return "escalate_to_human", {}


class StubLLMClient:
    """A deterministic, network-free :class:`LLMClient` implementation."""

    model = "stub-v1"

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        state = self._read_state(messages)
        text = json.dumps(self._decide(state))
        prompt_chars = sum(len(m.content) for m in messages)
        return LLMResponse(
            text=text,
            prompt_tokens=prompt_chars // 4,
            completion_tokens=len(text) // 4,
            latency_ms=0.0,
            model=self.model,
            cached=False,
        )

    @staticmethod
    def _read_state(messages: list[LLMMessage]) -> dict[str, Any]:
        for message in reversed(messages):
            if message.role == "user":
                try:
                    return json.loads(message.content)
                except (ValueError, TypeError):
                    return {}
        return {}

    def _decide(self, state: dict[str, Any]) -> dict[str, Any]:
        case = state.get("case", {})
        history = state.get("history", [])
        tools = state.get("tools", [])
        tool_names = {t.get("name") for t in tools}
        called = {h.get("tool") for h in history}
        effect_taken = any(
            h.get("tool") in {"create_payment_link", "refund_payment", "escalate_to_human"}
            for h in history
        )

        if effect_taken:
            return {"thought": "resolved", "final": {"resolution": self._last_effect(history)}}

        if "fetch_payment" in tool_names and "fetch_payment" not in called:
            return {
                "thought": "inspect the payment",
                "action": {"tool": "fetch_payment", "args": {"payment_id": case.get("payment_id", "")}},
            }

        if (
            "fetch_customer_history" in tool_names
            and case.get("customer_id")
            and "fetch_customer_history" not in called
        ):
            return {
                "thought": "check customer history",
                "action": {
                    "tool": "fetch_customer_history",
                    "args": {"customer_id": case.get("customer_id")},
                },
            }

        action_name, _ = _resolving_action(case.get("failure_reason", ""), tool_names)
        if action_name == "create_payment_link":
            return {
                "thought": "let the customer retry via a new link",
                "action": {
                    "tool": "create_payment_link",
                    "args": {"amount_inr": case.get("amount_inr", 0)},
                },
            }
        return {
            "thought": "needs a human",
            "action": {"tool": "escalate_to_human", "args": {}},
        }

    @staticmethod
    def _last_effect(history: list[dict[str, Any]]) -> str | None:
        for step in reversed(history):
            if step.get("tool") in {
                "create_payment_link",
                "refund_payment",
                "escalate_to_human",
            }:
                return step.get("tool")
        return None
