"""Payment provider adapters.

Exposes the :class:`~providers.base.PaymentProvider` interface, the normalised
value objects and enums in :mod:`providers.types`, normalised errors, and the
adapters. Every adapter maps its provider-specific failure strings, statuses and
amounts onto the normalised vocabulary so nothing above this layer ever sees a
provider-native type.
"""
from __future__ import annotations

from providers.base import PaymentProvider
from providers.errors import ProviderAPIError, ProviderError, ResourceNotFound
from providers.fake import FakeProvider
from providers.razorpay import RazorpayAdapter
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

__all__ = [
    "CustomerHistory",
    "EventType",
    "FailureReason",
    "FakeProvider",
    "NormalisedEvent",
    "Order",
    "OrderStatus",
    "Payment",
    "PaymentLink",
    "PaymentLinkStatus",
    "PaymentMethod",
    "PaymentProvider",
    "PaymentStatus",
    "ProviderAPIError",
    "ProviderError",
    "RazorpayAdapter",
    "Refund",
    "RefundStatus",
    "ResourceNotFound",
]
