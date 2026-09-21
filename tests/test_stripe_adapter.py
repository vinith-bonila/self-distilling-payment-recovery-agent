"""Stripe-specific normalisation, error mapping, and end-to-end integration.

The point of these tests is the abstraction: Stripe's own strings, statuses and
smallest-unit amounts must be translated inside ``providers/`` and never appear
above it, and the existing recovery pipeline must accept Stripe with no
provider-specific branching.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from providers.errors import ProviderAPIError, ResourceNotFound
from providers.stripe import StripeProvider
from providers.types import (
    EventType,
    FailureReason,
    OrderStatus,
    PaymentLinkStatus,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
)
from recovery.app import create_app
from recovery.db import session_scope
from recovery.models import InternalEvent, RawWebhookEvent

_SECRET = "whsec_stripe"
_BASE = "https://api.stripe.com/v1"


def _provider(handler) -> StripeProvider:
    client = httpx.Client(base_url=_BASE, transport=httpx.MockTransport(handler))
    return StripeProvider("sk_test_x", _SECRET, client=client)


def _intent(decline_code=None, code=None, status="requires_payment_method"):
    error = {}
    if code:
        error["code"] = code
    if decline_code:
        error["decline_code"] = decline_code
    return {
        "id": "pi_1",
        "amount": 250000,
        "currency": "inr",
        "status": status,
        "customer": "cus_1",
        "payment_method_types": ["card"],
        "metadata": {"order_id": "cs_1"},
        "last_payment_error": error or None,
        "created": 1_690_000_000,
    }


# --- failure-reason normalisation ---------------------------------------


@pytest.mark.parametrize(
    "decline_code,expected",
    [
        ("insufficient_funds", FailureReason.INSUFFICIENT_FUNDS),
        ("generic_decline", FailureReason.CARD_DECLINED),
        ("do_not_honor", FailureReason.CARD_DECLINED),
        ("expired_card", FailureReason.EXPIRED_CARD),
        ("authentication_required", FailureReason.AUTHENTICATION_REQUIRED),
        ("processing_error", FailureReason.PROCESSING_ERROR),
        ("incorrect_cvc", FailureReason.INVALID_DETAILS),
        ("stolen_card", FailureReason.RISK_BLOCKED),
        ("something_new_from_stripe", FailureReason.UNKNOWN),
    ],
)
def test_stripe_decline_codes_normalise(decline_code, expected) -> None:
    provider = _provider(lambda r: httpx.Response(200, json=_intent(decline_code=decline_code)))
    payment = provider.get_payment("pi_1")
    assert payment.failure_reason is expected
    # The Stripe string itself never escapes the adapter.
    assert type(payment.failure_reason).__module__ == "providers.types"


def test_stripe_falls_back_to_error_code_when_no_decline_code() -> None:
    provider = _provider(lambda r: httpx.Response(200, json=_intent(code="expired_card")))
    assert provider.get_payment("pi_1").failure_reason is FailureReason.EXPIRED_CARD


def test_succeeded_intent_has_no_failure_reason() -> None:
    provider = _provider(lambda r: httpx.Response(200, json=_intent(status="succeeded")))
    payment = provider.get_payment("pi_1")
    assert payment.status is PaymentStatus.CAPTURED
    assert payment.failure_reason is None


# --- normalised objects --------------------------------------------------


def test_payment_is_normalised_to_rupees_and_enums() -> None:
    provider = _provider(lambda r: httpx.Response(200, json=_intent(decline_code="insufficient_funds")))
    payment = provider.get_payment("pi_1")
    assert payment.amount_inr == 2500.0  # 250000 minor units -> rupees
    assert payment.currency == "INR"
    assert payment.status is PaymentStatus.FAILED
    assert payment.method is PaymentMethod.CARD
    assert payment.order_id == "cs_1"  # from metadata
    assert payment.customer_id == "cus_1"


# --- provider-specific error mapping -------------------------------------


def test_missing_resource_maps_to_resource_not_found() -> None:
    provider = _provider(lambda r: httpx.Response(404, json={"error": {"code": "resource_missing"}}))
    with pytest.raises(ResourceNotFound):
        provider.get_payment("pi_missing")


def test_server_error_maps_to_provider_api_error() -> None:
    provider = _provider(lambda r: httpx.Response(500, text="stripe is down"))
    with pytest.raises(ProviderAPIError) as excinfo:
        provider.get_payment("pi_1")
    assert excinfo.value.status_code == 500


def test_adapter_raises_no_httpx_errors_above_the_boundary() -> None:
    provider = _provider(lambda r: httpx.Response(402, json={"error": {"code": "card_declined"}}))
    with pytest.raises(ProviderAPIError):
        provider.get_payment("pi_1")


# --- webhook event normalisation -----------------------------------------


def _signed_event(secret: str = _SECRET) -> tuple[bytes, str]:
    body = {
        "id": "evt_1",
        "type": "payment_intent.payment_failed",
        "created": 1_690_000_000,
        "data": {"object": _intent(decline_code="insufficient_funds")},
    }
    payload = json.dumps(body).encode("utf-8")
    signed = b"1690000000." + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return payload, f"t=1690000000,v1={digest}"


def test_webhook_event_normalisation() -> None:
    provider = _provider(lambda r: httpx.Response(404))
    payload, _ = _signed_event()
    event = provider.parse_event(payload)
    assert event.provider == "stripe"
    assert event.event_id == "evt_1"
    assert event.event_type is EventType.PAYMENT_FAILED
    assert event.failure_reason is FailureReason.INSUFFICIENT_FUNDS
    assert event.amount_inr == 2500.0
    assert event.payment_id == "pi_1"


# --- end to end: recovery accepts Stripe with no branching ---------------


def test_stripe_webhook_flows_through_unchanged_recovery_pipeline(tmp_path) -> None:
    provider = _provider(lambda r: httpx.Response(404))
    app = create_app(
        provider_registry={"stripe": provider},
        database_url=f"sqlite:///{(tmp_path / 'stripe.db').as_posix()}",
    )
    payload, signature = _signed_event()
    with TestClient(app) as client:
        ok = client.post(
            "/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature}
        )
        assert ok.status_code == 200
        bad = client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": "t=1690000000,v1=" + "0" * 64},
        )
        assert bad.status_code == 400  # signature rejected at the boundary

    with session_scope() as session:
        assert session.query(RawWebhookEvent).count() == 1  # only the valid one
        event = session.query(InternalEvent).one()
        assert event.provider == "stripe"
        assert event.failure_reason == "insufficient_funds"  # normalised enum value
        assert event.amount_inr == 2500.0


# --- documented approximations, tested directly -------------------------
# PROVIDERS.md records where Stripe's model differs from Razorpay's. Each of
# those normalisation decisions is asserted here, not just described.

@pytest.mark.parametrize(
    "session_status,expected",
    [("open", OrderStatus.ATTEMPTED), ("complete", OrderStatus.PAID), ("expired", OrderStatus.CREATED)],
)
def test_order_is_a_checkout_session(session_status, expected) -> None:
    session = {"id": "cs_1", "amount_total": 250000, "currency": "inr", "status": session_status}
    provider = _provider(lambda r: httpx.Response(200, json=session))
    order = provider.get_order("cs_1")
    assert order.status is expected
    assert order.amount_inr == 2500.0
    assert order.currency == "INR"


def test_payment_link_is_a_checkout_session_with_inline_amount() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["form"] = dict(httpx.QueryParams(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"id": "cs_2", "url": "https://checkout.stripe.com/c/pay/cs_2",
                  "amount_total": 250000, "status": "open"},
        )

    link = _provider(handler).create_payment_link(2500.0, reference_id="pi_1")
    # Not the Payment Links API: a Checkout Session with inline price_data.
    assert seen["path"].endswith("/checkout/sessions")
    assert seen["form"]["mode"] == "payment"
    assert seen["form"]["line_items[0][price_data][unit_amount]"] == "250000"  # minor units
    assert seen["form"]["line_items[0][price_data][currency]"] == "inr"
    assert seen["form"]["client_reference_id"] == "pi_1"
    assert link.status is PaymentLinkStatus.CREATED
    assert link.short_url.startswith("https://checkout.stripe.com/")
    assert link.amount_inr == 2500.0


@pytest.mark.parametrize(
    "refund_status,expected",
    [("succeeded", RefundStatus.PROCESSED), ("pending", RefundStatus.PENDING), ("failed", RefundStatus.FAILED)],
)
def test_refund_status_mapping(refund_status, expected) -> None:
    body = {"id": "re_1", "amount": 250000, "status": refund_status}
    refund = _provider(lambda r: httpx.Response(200, json=body)).refund_payment("pi_1", 2500.0, "refund:pi_1")
    assert refund.status is expected


def test_refund_sends_native_idempotency_key_and_minor_units() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["idem"] = request.headers.get("Idempotency-Key")
        seen["form"] = dict(httpx.QueryParams(request.content.decode("utf-8")))
        return httpx.Response(200, json={"id": "re_1", "amount": 250000, "status": "succeeded"})

    refund = _provider(handler).refund_payment("pi_1", 2500.0, "refund:pi_1")
    # The guardrail's idempotency key is forwarded as Stripe's native header.
    assert seen["idem"] == "refund:pi_1"
    assert seen["form"] == {"payment_intent": "pi_1", "amount": "250000"}
    assert refund.idempotency_key == "refund:pi_1"
    assert refund.amount_inr == 2500.0


def test_customer_history_counts_any_non_succeeded_intent_as_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/payment_intents"):
            assert request.url.params.get("customer") == "cus_1"  # server-side filter
            return httpx.Response(200, json={"data": [
                {"id": "a", "status": "succeeded", "created": 1},
                {"id": "b", "status": "requires_payment_method", "created": 2},
                {"id": "c", "status": "canceled", "created": 3},
                {"id": "d", "status": "processing", "created": 4},  # coarse: counted failed
            ]})
        return httpx.Response(200, json={"id": "cus_1"})

    history = _provider(handler).get_customer_history("cus_1")
    assert (history.total_payments, history.successful_payments, history.failed_payments) == (4, 1, 3)


def test_bad_signature_produces_no_event_and_no_processing(tmp_path) -> None:
    provider = _provider(lambda r: httpx.Response(404))
    app = create_app(
        provider_registry={"stripe": provider},
        database_url=f"sqlite:///{(tmp_path / 'bad.db').as_posix()}",
    )
    payload, _ = _signed_event()
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/stripe", content=payload,
            headers={"Stripe-Signature": _signed_event(secret="whsec_attacker")[1]},
        )
        assert response.status_code == 400
    with session_scope() as session:
        assert session.query(RawWebhookEvent).count() == 0
        assert session.query(InternalEvent).count() == 0
