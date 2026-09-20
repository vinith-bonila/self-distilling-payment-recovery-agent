"""Build the primitive context a rule is evaluated against.

Converts normalised domain objects into a plain mapping of primitives (the only
thing the neutral grammar understands). Failure reason and method are passed as
their enum *values* — never provider strings. Fields whose data is not
available (e.g. customer history at the webhook boundary) are simply omitted, so
rules that need them do not match and the case escalates.
"""
from __future__ import annotations

from typing import Any

from providers.types import (
    CustomerHistory,
    FailureReason,
    NormalisedEvent,
    PaymentMethod,
)


def build_context(
    event: NormalisedEvent,
    *,
    method: PaymentMethod | None = None,
    history: CustomerHistory | None = None,
    hours_since_last_attempt: float | None = None,
    attempt_number: int | None = None,
) -> dict[str, Any]:
    """Return the rule-evaluation context for a normalised event."""
    reason = event.failure_reason or FailureReason.UNKNOWN
    context: dict[str, Any] = {
        "failure_reason": reason.value,
        "amount_inr": event.amount_inr,
    }
    if method is not None:
        context["method"] = method.value
    if history is not None:
        context["customer_successful_payments"] = history.successful_payments
        context["customer_failed_payments"] = history.failed_payments
    if hours_since_last_attempt is not None:
        context["hours_since_last_attempt"] = hours_since_last_attempt
    if attempt_number is not None:
        context["attempt_number"] = attempt_number
    return context
