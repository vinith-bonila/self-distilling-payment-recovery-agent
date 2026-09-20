"""Provider conformance suite.

One behavioural contract, exercised identically against every adapter. This
module is framework-neutral (plain assertions, no pytest import): a test module
supplies a :class:`ProviderHarness` per adapter and runs :data:`CHECKS` against
each. Adding a provider is then "one adapter file plus a harness".

A harness knows how to (a) build its adapter preloaded for a canonical
:class:`ProviderScenario` with all network mocked, and (b) produce a validly
signed webhook body plus a tampered signature.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from providers.base import PaymentProvider
from providers.errors import ResourceNotFound
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

_NORMALISED_MODULE = "providers.types"


@dataclass(frozen=True)
class ProviderScenario:
    """A canonical failed-payment scenario every harness reproduces."""

    payment_id: str = "pay_TEST1"
    order_id: str = "order_TEST1"
    customer_id: str = "cust_TEST1"
    amount_inr: float = 2500.0
    currency: str = "INR"
    failure_reason: FailureReason = FailureReason.INSUFFICIENT_FUNDS
    method: PaymentMethod = PaymentMethod.CARD
    successful_payments: int = 7
    failed_payments: int = 3
    event_id: str = "evt_TEST1"


SCENARIO = ProviderScenario()


class ProviderHarness(Protocol):
    """What a test supplies per adapter so the contract can run against it."""

    name: str

    def build(self, scenario: ProviderScenario) -> PaymentProvider:
        """Return an adapter preloaded (network mocked) for ``scenario``."""
        ...

    def signed_failed_event(self, scenario: ProviderScenario) -> tuple[bytes, str]:
        """Return ``(payload, valid_signature)`` for a failed-payment webhook."""
        ...

    def tampered_signature(self, payload: bytes) -> str:
        """Return a signature that must NOT verify against ``payload``."""
        ...


def _expect_raises(exc: type[BaseException], fn: Callable[[], object]) -> None:
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} to be raised")


# --- contract checks -----------------------------------------------------


def check_structural_conformance(h: ProviderHarness) -> None:
    provider = h.build(SCENARIO)
    assert isinstance(provider, PaymentProvider)
    assert isinstance(provider.name, str) and provider.name


def check_get_payment_is_normalised(h: ProviderHarness) -> None:
    payment = h.build(SCENARIO).get_payment(SCENARIO.payment_id)
    assert isinstance(payment, Payment)
    assert payment.amount_inr == SCENARIO.amount_inr  # rupees, not paise
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason is SCENARIO.failure_reason
    assert payment.method is SCENARIO.method
    assert payment.order_id == SCENARIO.order_id
    assert payment.customer_id == SCENARIO.customer_id


def check_get_order_is_normalised(h: ProviderHarness) -> None:
    order = h.build(SCENARIO).get_order(SCENARIO.order_id)
    assert isinstance(order, Order)
    assert order.amount_inr == SCENARIO.amount_inr
    assert isinstance(order.status, OrderStatus)


def check_customer_history_is_normalised(h: ProviderHarness) -> None:
    history = h.build(SCENARIO).get_customer_history(SCENARIO.customer_id)
    assert isinstance(history, CustomerHistory)
    assert history.successful_payments == SCENARIO.successful_payments
    assert history.failed_payments == SCENARIO.failed_payments
    assert history.total_payments == (
        SCENARIO.successful_payments + SCENARIO.failed_payments
    )


def check_create_payment_link_is_normalised(h: ProviderHarness) -> None:
    link = h.build(SCENARIO).create_payment_link(
        SCENARIO.amount_inr, description="retry your payment"
    )
    assert isinstance(link, PaymentLink)
    assert link.amount_inr == SCENARIO.amount_inr
    assert link.status is PaymentLinkStatus.CREATED
    assert link.short_url


def check_refund_is_normalised(h: ProviderHarness) -> None:
    refund = h.build(SCENARIO).refund_payment(
        SCENARIO.payment_id, SCENARIO.amount_inr, idempotency_key="idem-abc"
    )
    assert isinstance(refund, Refund)
    assert refund.amount_inr == SCENARIO.amount_inr
    assert refund.status is RefundStatus.PROCESSED
    assert refund.idempotency_key == "idem-abc"  # key carried through


def check_parse_event_normalises_reason(h: ProviderHarness) -> None:
    provider = h.build(SCENARIO)
    payload, _ = h.signed_failed_event(SCENARIO)
    event = provider.parse_event(payload)
    assert isinstance(event, NormalisedEvent)
    assert event.event_type is EventType.PAYMENT_FAILED
    assert event.failure_reason is SCENARIO.failure_reason
    assert event.amount_inr == SCENARIO.amount_inr  # rupees
    assert event.payment_id == SCENARIO.payment_id


def check_no_provider_native_types_leak(h: ProviderHarness) -> None:
    """The architectural invariant: everything crossing the boundary is a
    ``providers.types`` object or enum — never an SDK/httpx/provider type."""
    provider = h.build(SCENARIO)
    payment = provider.get_payment(SCENARIO.payment_id)
    assert type(payment).__module__ == _NORMALISED_MODULE
    assert type(payment.status).__module__ == _NORMALISED_MODULE
    assert type(payment.failure_reason).__module__ == _NORMALISED_MODULE
    assert type(payment.method).__module__ == _NORMALISED_MODULE

    payload, _ = h.signed_failed_event(SCENARIO)
    event = provider.parse_event(payload)
    assert type(event).__module__ == _NORMALISED_MODULE
    assert type(event.event_type).__module__ == _NORMALISED_MODULE


def check_missing_resource_raises_normalised_error(h: ProviderHarness) -> None:
    provider = h.build(SCENARIO)
    # A normalised ResourceNotFound, never an httpx/SDK-native error.
    _expect_raises(ResourceNotFound, lambda: provider.get_payment("pay_MISSING"))


def check_signature_valid_and_tampered(h: ProviderHarness) -> None:
    provider = h.build(SCENARIO)
    payload, signature = h.signed_failed_event(SCENARIO)
    assert provider.verify_signature(payload, signature) is True
    assert provider.verify_signature(payload, h.tampered_signature(payload)) is False


CHECKS: list[Callable[[ProviderHarness], None]] = [
    check_structural_conformance,
    check_get_payment_is_normalised,
    check_get_order_is_normalised,
    check_customer_history_is_normalised,
    check_create_payment_link_is_normalised,
    check_refund_is_normalised,
    check_parse_event_normalises_reason,
    check_no_provider_native_types_leak,
    check_missing_resource_raises_normalised_error,
    check_signature_valid_and_tampered,
]
