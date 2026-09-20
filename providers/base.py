"""Provider interface and the normalised failure-reason enum.

Phase 1 declares only what the skeleton needs (the enum, a minimal event shape,
and signature verification). The full :class:`PaymentProvider` interface and the
shared conformance suite arrive in Phase 3.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class FailureReason(enum.Enum):
    """Normalised payment-failure reasons shared across all providers.

    Adapters translate their own provider-specific strings onto this closed
    enum. Layers above ``providers`` reason only in terms of these members.
    """

    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_DECLINED = "card_declined"
    EXPIRED_CARD = "expired_card"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROCESSING_ERROR = "processing_error"
    INVALID_DETAILS = "invalid_details"
    RISK_BLOCKED = "risk_blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NormalisedEvent:
    """A provider event after signature check and normalisation.

    Kept intentionally small in Phase 1; extended in Phases 3-4.
    """

    provider: str
    event_id: str
    event_type: str
    payment_id: str
    amount_inr: float
    failure_reason: FailureReason
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """Interface every payment-provider adapter implements.

    Phase 1 declares only signature verification; Phase 3 adds event parsing,
    payment/order/customer fetches, payment-link creation and refunds, plus a
    conformance suite every adapter must pass identically.
    """

    name: str

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Return ``True`` iff ``signature`` authenticates ``payload``."""
        ...
