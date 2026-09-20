"""Normalised, provider-neutral domain types.

Everything above ``providers`` reasons in terms of these types and enums only.
No provider-specific string, status code, SDK object or paise/cents amount ever
appears here: amounts are rupees (INR) as ``float``, statuses and reasons are
closed enums, and every value object is one of these dataclasses.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class FailureReason(enum.Enum):
    """Normalised payment-failure reasons shared across all providers."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_DECLINED = "card_declined"
    EXPIRED_CARD = "expired_card"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROCESSING_ERROR = "processing_error"
    INVALID_DETAILS = "invalid_details"
    RISK_BLOCKED = "risk_blocked"
    UNKNOWN = "unknown"

    @classmethod
    def from_code(cls, code: str | None) -> "FailureReason":
        """Map a normalised code string onto a member, defaulting to UNKNOWN."""
        if not code:
            return cls.UNKNOWN
        try:
            return cls(code)
        except ValueError:
            return cls.UNKNOWN


class PaymentStatus(enum.Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethod(enum.Enum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMI = "emi"
    UNKNOWN = "unknown"


class OrderStatus(enum.Enum):
    CREATED = "created"
    ATTEMPTED = "attempted"
    PAID = "paid"


class RefundStatus(enum.Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class PaymentLinkStatus(enum.Enum):
    CREATED = "created"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class EventType(enum.Enum):
    """Normalised webhook event types."""

    PAYMENT_FAILED = "payment_failed"
    PAYMENT_AUTHORIZED = "payment_authorized"
    PAYMENT_CAPTURED = "payment_captured"
    REFUND_PROCESSED = "refund_processed"
    OTHER = "other"


@dataclass(frozen=True)
class Payment:
    """A payment, normalised. ``amount_inr`` is rupees, never paise."""

    id: str
    amount_inr: float
    currency: str
    status: PaymentStatus
    method: PaymentMethod
    order_id: str | None = None
    customer_id: str | None = None
    failure_reason: FailureReason | None = None
    created_at: datetime | None = None
    description: str = ""


@dataclass(frozen=True)
class Order:
    id: str
    amount_inr: float
    currency: str
    status: OrderStatus
    receipt: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class CustomerHistory:
    """Aggregate payment history for one customer (context for recovery)."""

    customer_id: str
    total_payments: int
    successful_payments: int
    failed_payments: int
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None


@dataclass(frozen=True)
class PaymentLink:
    id: str
    short_url: str
    amount_inr: float
    status: PaymentLinkStatus
    payment_id: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Refund:
    id: str
    payment_id: str
    amount_inr: float
    status: RefundStatus
    idempotency_key: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class NormalisedEvent:
    """A provider webhook after signature check and normalisation.

    Carries no raw provider payload: the raw bytes are persisted separately at
    the webhook boundary, so this object holds only normalised fields.
    """

    provider: str
    event_id: str
    event_type: EventType
    payment_id: str
    amount_inr: float
    failure_reason: FailureReason | None = None
    occurred_at: datetime | None = None
