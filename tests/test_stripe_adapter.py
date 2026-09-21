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
    PaymentMethod,
    PaymentStatus,
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
