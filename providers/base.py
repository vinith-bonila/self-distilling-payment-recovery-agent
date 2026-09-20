"""The ``PaymentProvider`` interface.

Every adapter (fake, Razorpay, later Stripe) implements this structural
protocol. All inputs and outputs use the normalised types in
:mod:`providers.types`; adapters never expose provider-native objects, strings
or amounts across this boundary.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# Re-exported so callers can import the whole normalised vocabulary from
# ``providers.base`` if they prefer a single import site.
from providers.types import (  # noqa: F401
    CustomerHistory,
    EventType,
    FailureReason,
    NormalisedEvent,
    Order,
    OrderStatus,
    Payment,
    PaymentLink,
    PaymentLinkStatus,
    PaymentMethod,
    PaymentStatus,
    Refund,
    RefundStatus,
)


@runtime_checkable
class PaymentProvider(Protocol):
    """Behavioural contract implemented by every provider adapter.

    The conformance suite (:mod:`providers.conformance`) exercises this contract
    identically against every adapter, with all network calls mocked.
    """

    name: str

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Return ``True`` iff ``signature`` authenticates the raw ``payload``."""
        ...

    def parse_event(
        self, payload: bytes, *, event_id: str | None = None
    ) -> NormalisedEvent:
        """Normalise a raw webhook body into a :class:`NormalisedEvent`.

        ``event_id`` may be supplied by the caller (e.g. from a provider header);
        adapters derive a deterministic one from the payload when it is absent.
        """
        ...

    def get_payment(self, payment_id: str) -> Payment:
        """Fetch a payment. Raises ``ResourceNotFound`` if it does not exist."""
        ...

    def get_order(self, order_id: str) -> Order:
        """Fetch an order. Raises ``ResourceNotFound`` if it does not exist."""
        ...

    def get_customer_history(self, customer_id: str) -> CustomerHistory:
        """Fetch aggregate payment history for a customer."""
        ...

    def create_payment_link(
        self,
        amount_inr: float,
        *,
        description: str = "",
        reference_id: str | None = None,
    ) -> PaymentLink:
        """Create a payment link the customer can use to retry payment."""
        ...

    def refund_payment(
        self, payment_id: str, amount_inr: float, idempotency_key: str
    ) -> Refund:
        """Refund a payment. ``idempotency_key`` ties the call to the guardrail
        idempotency record in ``agentcore``."""
        ...
