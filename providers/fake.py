"""In-memory fake provider.

Used by the offline eval harness and by tests. It holds no network state:
data is seeded via ``add_*`` helpers (which are not part of the
:class:`~providers.base.PaymentProvider` contract). Webhook signatures are real
HMAC-SHA256 over the raw body.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from providers.errors import ResourceNotFound
from providers.types import (
    CustomerHistory,
    EventType,
    FailureReason,
    NormalisedEvent,
    Order,
    Payment,
    PaymentLink,
    PaymentLinkStatus,
    PaymentMethod,
    Refund,
    RefundStatus,
)

_EVENT_TYPES = {
    "payment.failed": EventType.PAYMENT_FAILED,
    "payment.authorized": EventType.PAYMENT_AUTHORIZED,
    "payment.captured": EventType.PAYMENT_CAPTURED,
    "refund.processed": EventType.REFUND_PROCESSED,
}


class FakeProvider:
    """A deterministic, network-free provider implementing the full contract."""

    name = "fake"

    def __init__(self, webhook_secret: str = "whsec_fake") -> None:
        self._secret = webhook_secret.encode("utf-8")
        self._payments: dict[str, Payment] = {}
        self._orders: dict[str, Order] = {}
        self._histories: dict[str, CustomerHistory] = {}
        self._link_seq = 0
        self._refund_seq = 0

    # --- seeding helpers (not part of the PaymentProvider contract) ------

    def add_payment(self, payment: Payment) -> None:
        self._payments[payment.id] = payment

    def add_order(self, order: Order) -> None:
        self._orders[order.id] = order

    def add_customer_history(self, history: CustomerHistory) -> None:
        self._histories[history.customer_id] = history

    def build_failed_event_payload(
        self,
        *,
        event_id: str,
        payment_id: str,
        amount_inr: float,
        failure_reason: FailureReason,
        method: PaymentMethod,
        occurred_at: datetime | None = None,
    ) -> bytes:
        """Build a raw webhook body in the fake provider's own wire format."""
        occurred = occurred_at or datetime.now(timezone.utc)
        body = {
            "event_id": event_id,
            "event": "payment.failed",
            "payment_id": payment_id,
            "amount_inr": amount_inr,
            "failure_reason": failure_reason.value,
            "method": method.value,
            "occurred_at": occurred.isoformat(),
        }
        return json.dumps(body).encode("utf-8")

    # --- PaymentProvider contract ----------------------------------------

    def sign(self, payload: bytes) -> str:
        """Produce a valid signature for ``payload`` (test/eval helper)."""
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(payload), signature)

    def parse_event(
        self, payload: bytes, *, event_id: str | None = None
    ) -> NormalisedEvent:
        body = json.loads(payload)
        occurred_raw = body.get("occurred_at")
        return NormalisedEvent(
            provider=self.name,
            event_id=event_id or body["event_id"],
            event_type=_EVENT_TYPES.get(body.get("event", ""), EventType.OTHER),
            payment_id=body["payment_id"],
            amount_inr=float(body["amount_inr"]),
            failure_reason=FailureReason.from_code(body.get("failure_reason")),
            occurred_at=datetime.fromisoformat(occurred_raw) if occurred_raw else None,
        )

    def get_payment(self, payment_id: str) -> Payment:
        try:
            return self._payments[payment_id]
        except KeyError:
            raise ResourceNotFound(f"payment {payment_id!r} not found") from None

    def get_order(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise ResourceNotFound(f"order {order_id!r} not found") from None

    def get_customer_history(self, customer_id: str) -> CustomerHistory:
        try:
            return self._histories[customer_id]
        except KeyError:
            raise ResourceNotFound(
                f"customer {customer_id!r} not found"
            ) from None

    def create_payment_link(
        self,
        amount_inr: float,
        *,
        description: str = "",
        reference_id: str | None = None,
    ) -> PaymentLink:
        self._link_seq += 1
        link_id = f"plink_fake_{self._link_seq}"
        return PaymentLink(
            id=link_id,
            short_url=f"https://fake.pay/{link_id}",
            amount_inr=amount_inr,
            status=PaymentLinkStatus.CREATED,
            payment_id=reference_id,
        )

    def refund_payment(
        self, payment_id: str, amount_inr: float, idempotency_key: str
    ) -> Refund:
        self._refund_seq += 1
        return Refund(
            id=f"rfnd_fake_{self._refund_seq}",
            payment_id=payment_id,
            amount_inr=amount_inr,
            status=RefundStatus.PROCESSED,
            idempotency_key=idempotency_key,
            created_at=datetime.now(timezone.utc),
        )
