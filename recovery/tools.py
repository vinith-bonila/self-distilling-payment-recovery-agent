"""The six payment-recovery tools, wired to a provider and the guarded executor.

Read tools (`fetch_*`) call the provider directly and return a bounded, sanitised
dict. Effect tools (`create_payment_link`, `refund_payment`, `escalate_to_human`)
carry only an ``Action`` builder; the actual side effect is performed by an
effect handler registered on the ``GuardedExecutor``, so the only route to a side
effect is through the executor. Tool outputs use normalised, provider-neutral
types only.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agentcore.agent import ToolParam, ToolParamType, ToolRegistry, ToolSpec
from agentcore.guardrails import Action
from providers.base import PaymentProvider
from providers.errors import ResourceNotFound
from providers.types import CustomerHistory, Order, Payment, PaymentLink, Refund

_STR = ToolParamType.STRING
_NUM = ToolParamType.NUMBER


@dataclass(frozen=True)
class RecoveryCase:
    """The failed payment under recovery."""

    payment_id: str
    amount_inr: float
    failure_reason: str  # normalised FailureReason value
    method: str | None = None
    customer_id: str | None = None
    order_id: str | None = None


def bounded_payment(p: Payment) -> dict[str, Any]:
    return {
        "id": p.id,
        "amount_inr": p.amount_inr,
        "status": p.status.value,
        "method": p.method.value,
        "failure_reason": p.failure_reason.value if p.failure_reason else None,
        "order_id": p.order_id,
        "customer_id": p.customer_id,
    }


def bounded_order(o: Order) -> dict[str, Any]:
    return {"id": o.id, "amount_inr": o.amount_inr, "status": o.status.value}


def bounded_history(h: CustomerHistory) -> dict[str, Any]:
    return {
        "customer_id": h.customer_id,
        "total_payments": h.total_payments,
        "successful_payments": h.successful_payments,
        "failed_payments": h.failed_payments,
    }


def bounded_link(link: PaymentLink) -> dict[str, Any]:
    return {"id": link.id, "short_url": link.short_url, "status": link.status.value}


def bounded_refund(refund: Refund) -> dict[str, Any]:
    return {
        "id": refund.id,
        "amount_inr": refund.amount_inr,
        "status": refund.status.value,
    }


EffectHandler = Callable[[Action], Any]


def build_tools(
    case: RecoveryCase, provider: PaymentProvider
) -> tuple[ToolRegistry, dict[str, EffectHandler]]:
    """Build the tool registry and the executor effect handlers for one case."""
    subject = case.customer_id or case.payment_id
    registry = ToolRegistry()

    # --- read tools -------------------------------------------------------
    def fetch_payment(args: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return bounded_payment(provider.get_payment(args["payment_id"]))
        except ResourceNotFound:
            return {"error": "not_found"}

    def fetch_order(args: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return bounded_order(provider.get_order(args["order_id"]))
        except ResourceNotFound:
            return {"error": "not_found"}

    def fetch_customer_history(args: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return bounded_history(provider.get_customer_history(args["customer_id"]))
        except ResourceNotFound:
            return {"error": "not_found"}

    registry.add(
        ToolSpec("fetch_payment", "Fetch a payment by id.", {"payment_id": ToolParam(_STR)}, read_handler=fetch_payment)
    )
    registry.add(
        ToolSpec("fetch_order", "Fetch an order by id.", {"order_id": ToolParam(_STR)}, read_handler=fetch_order)
    )
    registry.add(
        ToolSpec(
            "fetch_customer_history",
            "Fetch a customer's payment history.",
            {"customer_id": ToolParam(_STR)},
            read_handler=fetch_customer_history,
        )
    )

    # --- effect tools (Action builders only; no direct side effect) -------
    registry.add(
        ToolSpec(
            "create_payment_link",
            "Create a new payment link for the customer to retry payment.",
            {"amount_inr": ToolParam(_NUM)},
            build_action=lambda a: Action(
                name="send_payment_link",
                subject_id=subject,
                idempotency_key=f"link:{case.payment_id}",
                cost=0.0,
                params={"amount_inr": float(a["amount_inr"])},
            ),
        )
    )
    def build_refund_action(_args: Mapping[str, Any]) -> Action:
        # Full refund only. The amount is derived server-side from the
        # authoritative payment record, never from model input: the model may
        # ask to refund, but it cannot choose (or inflate) the amount. Approval,
        # spend-cap and idempotency all operate on this server-derived amount.
        authoritative_amount = provider.get_payment(case.payment_id).amount_inr
        key = f"refund:{case.payment_id}"
        return Action(
            name="refund",
            subject_id=subject,
            idempotency_key=key,
            cost=authoritative_amount,
            params={"amount_inr": authoritative_amount, "idempotency_key": key},
        )

    registry.add(
        ToolSpec(
            "refund_payment",
            "Issue a FULL refund of the failed payment. The amount is derived "
            "server-side from the payment record; you cannot set it. Subject to "
            "the approval gate, spend cap and idempotency.",
            {},  # no model-controlled parameters
            build_action=build_refund_action,
        )
    )
    registry.add(
        ToolSpec(
            "escalate_to_human",
            "Escalate this payment to a human agent.",
            {},
            build_action=lambda a: Action(
                name="escalate_to_human",
                subject_id=subject,
                idempotency_key=f"escalate:{case.payment_id}",
                cost=0.0,
                params={},
            ),
        )
    )

    # --- effect handlers: the ONLY place a side effect happens ------------
    def do_link(action: Action) -> dict[str, Any]:
        link = provider.create_payment_link(
            action.params["amount_inr"], reference_id=case.payment_id
        )
        return bounded_link(link)

    def do_refund(action: Action) -> dict[str, Any]:
        refund = provider.refund_payment(
            case.payment_id, action.params["amount_inr"], action.params["idempotency_key"]
        )
        return bounded_refund(refund)

    def do_escalate(action: Action) -> dict[str, Any]:
        return {"status": "escalated", "payment_id": case.payment_id}

    handlers: dict[str, EffectHandler] = {
        "send_payment_link": do_link,
        "refund": do_refund,
        "escalate_to_human": do_escalate,
    }
    return registry, handlers
