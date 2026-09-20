"""Razorpay adapter over the Razorpay REST API using ``httpx``.

We deliberately do not depend on the ``razorpay`` SDK: the frozen stack allows
``httpx``, and talking to the REST API directly keeps the dependency surface
small and the calls trivially mockable (via ``httpx.MockTransport``).

All amounts cross the Razorpay boundary in paise (integer) and are converted to
rupees here. All provider status/reason strings are mapped to normalised enums
here. Callers above ``providers`` never see any of that.
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

_DEFAULT_BASE_URL = "https://api.razorpay.com/v1"

_METHOD_MAP = {
    "card": PaymentMethod.CARD,
    "upi": PaymentMethod.UPI,
    "netbanking": PaymentMethod.NETBANKING,
    "wallet": PaymentMethod.WALLET,
    "emi": PaymentMethod.EMI,
}

_PAYMENT_STATUS_MAP = {
    "created": PaymentStatus.CREATED,
    "authorized": PaymentStatus.AUTHORIZED,
    "captured": PaymentStatus.CAPTURED,
    "failed": PaymentStatus.FAILED,
    "refunded": PaymentStatus.REFUNDED,
}

_ORDER_STATUS_MAP = {
    "created": OrderStatus.CREATED,
    "attempted": OrderStatus.ATTEMPTED,
    "paid": OrderStatus.PAID,
}

_REFUND_STATUS_MAP = {
    "pending": RefundStatus.PENDING,
    "processed": RefundStatus.PROCESSED,
    "failed": RefundStatus.FAILED,
}

_LINK_STATUS_MAP = {
    "created": PaymentLinkStatus.CREATED,
    "paid": PaymentLinkStatus.PAID,
    "expired": PaymentLinkStatus.EXPIRED,
    "cancelled": PaymentLinkStatus.CANCELLED,
}

_EVENT_TYPE_MAP = {
    "payment.failed": EventType.PAYMENT_FAILED,
    "payment.authorized": EventType.PAYMENT_AUTHORIZED,
    "payment.captured": EventType.PAYMENT_CAPTURED,
    "refund.processed": EventType.REFUND_PROCESSED,
}

# Razorpay surfaces a free-form ``error_reason``; we map the common ones and
# fall back to UNKNOWN. This mapping is intentionally conservative and is the
# only place Razorpay reason strings are interpreted.
_REASON_MAP = {
    "insufficient_funds": FailureReason.INSUFFICIENT_FUNDS,
    "insufficient_balance": FailureReason.INSUFFICIENT_FUNDS,
    "payment_declined": FailureReason.CARD_DECLINED,
    "card_declined": FailureReason.CARD_DECLINED,
    "expired_card": FailureReason.EXPIRED_CARD,
    "authentication_failed": FailureReason.AUTHENTICATION_REQUIRED,
    "authentication_required": FailureReason.AUTHENTICATION_REQUIRED,
    "gateway_error": FailureReason.PROCESSING_ERROR,
    "server_error": FailureReason.PROCESSING_ERROR,
    "invalid_card": FailureReason.INVALID_DETAILS,
    "invalid_details": FailureReason.INVALID_DETAILS,
    "payment_frozen": FailureReason.RISK_BLOCKED,
    "risk_blocked": FailureReason.RISK_BLOCKED,
}


def _paise_to_inr(paise: int) -> float:
    return paise / 100.0


def _inr_to_paise(amount_inr: float) -> int:
    return int(round(amount_inr * 100))


def _ts_to_dt(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


class RazorpayAdapter:
    """Razorpay implementation of :class:`~providers.base.PaymentProvider`."""

    name = "razorpay"
    signature_header = "X-Razorpay-Signature"
    event_id_header = "X-Razorpay-Event-Id"

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        # Defence in depth: config already enforces sandbox-only keys, but the
        # adapter is what would make live calls, so it refuses non-test keys too.
        if key_id and not key_id.startswith("rzp_test_"):
            raise ValueError(
                "RazorpayAdapter refuses a non-sandbox key_id (must be rzp_test_...)."
            )
        self._webhook_secret = webhook_secret.encode("utf-8")
        self._client = client or httpx.Client(
            base_url=base_url, auth=(key_id, key_secret), timeout=10.0
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

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=body)
        self._raise_for_status(response)
        return response.json()

    # --- PaymentProvider contract ----------------------------------------

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        expected = hmac.new(self._webhook_secret, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_event(
        self, payload: bytes, *, event_id: str | None = None
    ) -> NormalisedEvent:
        body = json.loads(payload)
        event = body.get("event", "")
        entity = body.get("payload", {}).get("payment", {}).get("entity", {})
        return NormalisedEvent(
            provider=self.name,
            event_id=event_id or body.get("id") or f"{event}:{entity.get('id')}",
            event_type=_EVENT_TYPE_MAP.get(event, EventType.OTHER),
            payment_id=entity.get("id", ""),
            amount_inr=_paise_to_inr(int(entity.get("amount", 0))),
            failure_reason=_REASON_MAP.get(
                entity.get("error_reason"), FailureReason.UNKNOWN
            )
            if entity.get("status") == "failed"
            else None,
            occurred_at=_ts_to_dt(entity.get("created_at")),
        )

    def _to_payment(self, d: dict[str, Any]) -> Payment:
        return Payment(
            id=d["id"],
            amount_inr=_paise_to_inr(int(d["amount"])),
            currency=d.get("currency", "INR"),
            status=_PAYMENT_STATUS_MAP.get(d.get("status"), PaymentStatus.CREATED),
            method=_METHOD_MAP.get(d.get("method"), PaymentMethod.UNKNOWN),
            order_id=d.get("order_id"),
            customer_id=d.get("customer_id"),
            failure_reason=(
                _REASON_MAP.get(d.get("error_reason"), FailureReason.UNKNOWN)
                if d.get("status") == "failed"
                else None
            ),
            created_at=_ts_to_dt(d.get("created_at")),
            description=d.get("description") or "",
        )

    def get_payment(self, payment_id: str) -> Payment:
        return self._to_payment(self._get(f"/payments/{payment_id}"))

    def get_order(self, order_id: str) -> Order:
        d = self._get(f"/orders/{order_id}")
        return Order(
            id=d["id"],
            amount_inr=_paise_to_inr(int(d["amount"])),
            currency=d.get("currency", "INR"),
            status=_ORDER_STATUS_MAP.get(d.get("status"), OrderStatus.CREATED),
            receipt=d.get("receipt"),
            created_at=_ts_to_dt(d.get("created_at")),
        )

    def get_customer_history(self, customer_id: str) -> CustomerHistory:
        # Confirm the customer exists (404 -> ResourceNotFound), then aggregate
        # from the payments listing. Razorpay has no single "customer history"
        # endpoint; production would page this or keep a local index.
        self._get(f"/customers/{customer_id}")
        listing = self._get("/payments", params={"count": 100})
        items = [
            i for i in listing.get("items", []) if i.get("customer_id") == customer_id
        ]
        captured = [i for i in items if i.get("status") == "captured"]
        failed = [i for i in items if i.get("status") == "failed"]
        return CustomerHistory(
            customer_id=customer_id,
            total_payments=len(items),
            successful_payments=len(captured),
            failed_payments=len(failed),
            last_success_at=_ts_to_dt(
                max((i.get("created_at", 0) for i in captured), default=0) or None
            ),
            last_failure_at=_ts_to_dt(
                max((i.get("created_at", 0) for i in failed), default=0) or None
            ),
        )

    def create_payment_link(
        self,
        amount_inr: float,
        *,
        description: str = "",
        reference_id: str | None = None,
    ) -> PaymentLink:
        body: dict[str, Any] = {
            "amount": _inr_to_paise(amount_inr),
            "currency": "INR",
            "description": description,
        }
        if reference_id:
            body["reference_id"] = reference_id
        d = self._post("/payment_links", body)
        return PaymentLink(
            id=d["id"],
            short_url=d.get("short_url", ""),
            amount_inr=_paise_to_inr(int(d["amount"])),
            status=_LINK_STATUS_MAP.get(d.get("status"), PaymentLinkStatus.CREATED),
            payment_id=reference_id,
            expires_at=_ts_to_dt(d.get("expire_by")),
        )

    def refund_payment(
        self, payment_id: str, amount_inr: float, idempotency_key: str
    ) -> Refund:
        d = self._post(
            f"/payments/{payment_id}/refund",
            {"amount": _inr_to_paise(amount_inr)},
        )
        return Refund(
            id=d["id"],
            payment_id=payment_id,
            amount_inr=_paise_to_inr(int(d["amount"])),
            status=_REFUND_STATUS_MAP.get(d.get("status"), RefundStatus.PENDING),
            idempotency_key=idempotency_key,
            created_at=_ts_to_dt(d.get("created_at")),
        )
