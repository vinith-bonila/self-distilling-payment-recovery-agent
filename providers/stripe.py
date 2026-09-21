"""Stripe adapter over the Stripe REST API using ``httpx``.

As with Razorpay we deliberately do not depend on the ``stripe`` SDK: ``httpx``
is in the frozen stack and the REST calls mock cleanly. All Stripe-specific
structures, status strings, decline codes and smallest-unit amounts are
translated to the normalised vocabulary here and never cross this boundary.

Honest mapping notes (Stripe has no 1:1 equivalent for some concepts):

* **Payment** maps to a PaymentIntent. Its ``order_id`` is read from
  ``metadata.order_id`` — Stripe PaymentIntents carry no first-class order.
* **Order** maps to a Checkout Session. Stripe's Orders API is not part of the
  modern payments surface, and a Checkout Session is the closest object
  representing an intended purchase.
* **Payment link** is created as a Checkout Session, not via the Payment Links
  API: Payment Links require pre-created Price objects, whereas Checkout
  Sessions accept an inline ``price_data`` amount, which is what recovery needs.
* **Customer history** uses the PaymentIntents list filtered server-side by
  ``customer``; "failed" is counted as any non-succeeded intent returned.

What is implemented and tested is exactly the PaymentProvider contract against
mocked HTTP. This is not full production Stripe coverage.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from providers.errors import ProviderAPIError, ResourceNotFound
from providers.types import (
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

_DEFAULT_BASE_URL = "https://api.stripe.com/v1"

_PAYMENT_STATUS_MAP = {
    "succeeded": PaymentStatus.CAPTURED,
    "requires_payment_method": PaymentStatus.FAILED,
    "canceled": PaymentStatus.FAILED,
    "processing": PaymentStatus.CREATED,
    "requires_action": PaymentStatus.AUTHORIZED,
    "requires_confirmation": PaymentStatus.AUTHORIZED,
    "requires_capture": PaymentStatus.AUTHORIZED,
}

_ORDER_STATUS_MAP = {
    "open": OrderStatus.ATTEMPTED,
    "complete": OrderStatus.PAID,
    "expired": OrderStatus.CREATED,
}

_LINK_STATUS_MAP = {
    "open": PaymentLinkStatus.CREATED,
    "complete": PaymentLinkStatus.PAID,
    "expired": PaymentLinkStatus.EXPIRED,
}

_REFUND_STATUS_MAP = {
    "succeeded": RefundStatus.PROCESSED,
    "pending": RefundStatus.PENDING,
    "failed": RefundStatus.FAILED,
    "canceled": RefundStatus.FAILED,
}

_METHOD_MAP = {
    "card": PaymentMethod.CARD,
    "upi": PaymentMethod.UPI,
    "netbanking": PaymentMethod.NETBANKING,
    "wallet": PaymentMethod.WALLET,
    "link": PaymentMethod.WALLET,
}

_EVENT_TYPE_MAP = {
    "payment_intent.payment_failed": EventType.PAYMENT_FAILED,
    "payment_intent.succeeded": EventType.PAYMENT_CAPTURED,
    "payment_intent.amount_capturable_updated": EventType.PAYMENT_AUTHORIZED,
    "charge.refunded": EventType.REFUND_PROCESSED,
}

# Stripe reports a ``code`` and often a card ``decline_code``; we prefer the
# decline code. This is the only place Stripe error strings are interpreted.
_REASON_MAP = {
    "insufficient_funds": FailureReason.INSUFFICIENT_FUNDS,
    "card_declined": FailureReason.CARD_DECLINED,
    "generic_decline": FailureReason.CARD_DECLINED,
    "do_not_honor": FailureReason.CARD_DECLINED,
    "transaction_not_allowed": FailureReason.CARD_DECLINED,
    "expired_card": FailureReason.EXPIRED_CARD,
    "authentication_required": FailureReason.AUTHENTICATION_REQUIRED,
    "payment_intent_authentication_failure": FailureReason.AUTHENTICATION_REQUIRED,
    "processing_error": FailureReason.PROCESSING_ERROR,
    "try_again_later": FailureReason.PROCESSING_ERROR,
    "incorrect_number": FailureReason.INVALID_DETAILS,
    "incorrect_cvc": FailureReason.INVALID_DETAILS,
    "invalid_expiry_month": FailureReason.INVALID_DETAILS,
    "invalid_expiry_year": FailureReason.INVALID_DETAILS,
    "invalid_account": FailureReason.INVALID_DETAILS,
    "fraudulent": FailureReason.RISK_BLOCKED,
    "lost_card": FailureReason.RISK_BLOCKED,
    "stolen_card": FailureReason.RISK_BLOCKED,
    "merchant_blacklist": FailureReason.RISK_BLOCKED,
}


def _minor_to_inr(minor: int) -> float:
    return minor / 100.0


def _inr_to_minor(amount_inr: float) -> int:
    return int(round(amount_inr * 100))


def _ts_to_dt(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _reason_from_error(error: dict[str, Any] | None) -> FailureReason:
    if not error:
        return FailureReason.UNKNOWN
    code = error.get("decline_code") or error.get("code")
    return _REASON_MAP.get(code, FailureReason.UNKNOWN)


class StripeProvider:
    """Stripe implementation of :class:`~providers.base.PaymentProvider`."""

    name = "stripe"
    signature_header = "Stripe-Signature"
    event_id_header = None  # Stripe carries the event id in the body

    def __init__(
        self,
        api_key: str,
        webhook_secret: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        # Defence in depth: config enforces sandbox-only keys, but the adapter
        # is what would make live calls, so it refuses non-test keys too.
        if api_key and not api_key.startswith("sk_test_"):
            raise ValueError(
                "StripeProvider refuses a non-sandbox api_key (must be sk_test_...)."
            )
        self._webhook_secret = webhook_secret.encode("utf-8")
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )

    # --- HTTP plumbing ---------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 404:
            raise ResourceNotFound(response.text)
        if response.status_code >= 400:
            raise ProviderAPIError(response.status_code, response.text)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.get(path, params=params)
        self._raise_for_status(response)
        return response.json()

    def _post(
        self, path: str, data: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        # Stripe takes form-encoded bodies, not JSON.
        response = self._client.post(path, data=data, headers=headers)
        self._raise_for_status(response)
        return response.json()

    # --- PaymentProvider contract ----------------------------------------

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify a ``Stripe-Signature`` header (``t=<ts>,v1=<hmac>``).

        The signed payload is ``"{timestamp}.{raw_body}"`` HMAC-SHA256'd with the
        endpoint's signing secret, per Stripe's documented scheme.
        """
        try:
            parts = dict(
                item.split("=", 1) for item in signature.split(",") if "=" in item
            )
        except ValueError:
            return False
        timestamp, provided = parts.get("t"), parts.get("v1")
        if not timestamp or not provided:
            return False
        signed_payload = f"{timestamp}.".encode("utf-8") + payload
        expected = hmac.new(
            self._webhook_secret, signed_payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, provided)

    def parse_event(
        self, payload: bytes, *, event_id: str | None = None
    ) -> NormalisedEvent:
        body = json.loads(payload)
        event_type = body.get("type", "")
        entity = body.get("data", {}).get("object", {})
        status = entity.get("status")
        error = entity.get("last_payment_error")
        return NormalisedEvent(
            provider=self.name,
            event_id=event_id or body.get("id") or f"{event_type}:{entity.get('id')}",
            event_type=_EVENT_TYPE_MAP.get(event_type, EventType.OTHER),
            payment_id=entity.get("id", ""),
            amount_inr=_minor_to_inr(int(entity.get("amount", 0))),
            failure_reason=(
                _reason_from_error(error)
                if event_type == "payment_intent.payment_failed" or error
                else None
            ),
            occurred_at=_ts_to_dt(body.get("created") or entity.get("created")),
        )

    def _to_payment(self, d: dict[str, Any]) -> Payment:
        status = _PAYMENT_STATUS_MAP.get(d.get("status"), PaymentStatus.CREATED)
        error = d.get("last_payment_error")
        method_type = (d.get("payment_method_types") or ["card"])[0]
        return Payment(
            id=d["id"],
            amount_inr=_minor_to_inr(int(d["amount"])),
            currency=(d.get("currency") or "inr").upper(),
            status=status,
            method=_METHOD_MAP.get(method_type, PaymentMethod.UNKNOWN),
            order_id=(d.get("metadata") or {}).get("order_id"),
            customer_id=d.get("customer"),
            failure_reason=(
                _reason_from_error(error)
                if (status is PaymentStatus.FAILED or error)
                else None
            ),
            created_at=_ts_to_dt(d.get("created")),
            description=d.get("description") or "",
        )

    def get_payment(self, payment_id: str) -> Payment:
        return self._to_payment(self._get(f"/payment_intents/{payment_id}"))

    def get_order(self, order_id: str) -> Order:
        """Stripe has no Razorpay-style order; we use a Checkout Session."""
        d = self._get(f"/checkout/sessions/{order_id}")
        return Order(
            id=d["id"],
            amount_inr=_minor_to_inr(int(d.get("amount_total", 0))),
            currency=(d.get("currency") or "inr").upper(),
            status=_ORDER_STATUS_MAP.get(d.get("status"), OrderStatus.CREATED),
            receipt=(d.get("metadata") or {}).get("receipt"),
            created_at=_ts_to_dt(d.get("created")),
        )

    def get_customer_history(self, customer_id: str) -> CustomerHistory:
        self._get(f"/customers/{customer_id}")  # 404 -> ResourceNotFound
        listing = self._get(
            "/payment_intents", params={"customer": customer_id, "limit": 100}
        )
        items = listing.get("data", [])
        succeeded = [i for i in items if i.get("status") == "succeeded"]
        failed = [i for i in items if i.get("status") != "succeeded"]
        return CustomerHistory(
            customer_id=customer_id,
            total_payments=len(items),
            successful_payments=len(succeeded),
            failed_payments=len(failed),
            last_success_at=_ts_to_dt(
                max((i.get("created", 0) for i in succeeded), default=0) or None
            ),
            last_failure_at=_ts_to_dt(
                max((i.get("created", 0) for i in failed), default=0) or None
            ),
        )

    def create_payment_link(
        self,
        amount_inr: float,
        *,
        description: str = "",
        reference_id: str | None = None,
    ) -> PaymentLink:
        """Create a hosted Checkout Session URL for the customer to retry."""
        minor = _inr_to_minor(amount_inr)
        data: dict[str, Any] = {
            "mode": "payment",
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": "inr",
            "line_items[0][price_data][unit_amount]": minor,
            "line_items[0][price_data][product_data][name]": description
            or "Payment retry",
        }
        if reference_id:
            data["client_reference_id"] = reference_id
        d = self._post("/checkout/sessions", data)
        return PaymentLink(
            id=d["id"],
            short_url=d.get("url", ""),
            amount_inr=_minor_to_inr(int(d.get("amount_total", minor))),
            status=_LINK_STATUS_MAP.get(d.get("status"), PaymentLinkStatus.CREATED),
            payment_id=reference_id,
            expires_at=_ts_to_dt(d.get("expires_at")),
        )

    def refund_payment(
        self, payment_id: str, amount_inr: float, idempotency_key: str
    ) -> Refund:
        """Refund a PaymentIntent. Stripe supports a native idempotency header."""
        d = self._post(
            "/refunds",
            {"payment_intent": payment_id, "amount": _inr_to_minor(amount_inr)},
            headers={"Idempotency-Key": idempotency_key},
        )
        return Refund(
            id=d["id"],
            payment_id=payment_id,
            amount_inr=_minor_to_inr(int(d["amount"])),
            status=_REFUND_STATUS_MAP.get(d.get("status"), RefundStatus.PENDING),
            idempotency_key=idempotency_key,
            created_at=_ts_to_dt(d.get("created")),
        )
