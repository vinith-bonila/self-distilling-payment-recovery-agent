"""Run the one provider conformance contract against every adapter.

The ``fake`` harness seeds in-memory data; the ``razorpay`` harness mocks the
Razorpay REST API with ``httpx.MockTransport`` (no network). Both must satisfy
every check in ``providers.conformance.CHECKS`` identically.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest

from providers.conformance import CHECKS, SCENARIO, ProviderScenario
from providers.fake import FakeProvider
from providers.razorpay import RazorpayAdapter
from providers.types import (
    CustomerHistory,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
)

_FAKE_SECRET = "whsec_fake"
_RZP_SECRET = "whsec_rzp"
_RZP_BASE = "https://api.razorpay.com/v1"


class FakeHarness:
    name = "fake"

    def build(self, scenario: ProviderScenario) -> FakeProvider:
        provider = FakeProvider(webhook_secret=_FAKE_SECRET)
        provider.add_payment(
            Payment(
                id=scenario.payment_id,
                amount_inr=scenario.amount_inr,
                currency=scenario.currency,
                status=PaymentStatus.FAILED,
                method=scenario.method,
                order_id=scenario.order_id,
                customer_id=scenario.customer_id,
                failure_reason=scenario.failure_reason,
                created_at=datetime.now(timezone.utc),
            )
        )
        provider.add_order(
            Order(
                id=scenario.order_id,
                amount_inr=scenario.amount_inr,
                currency=scenario.currency,
                status=OrderStatus.ATTEMPTED,
                receipt="rcpt_1",
            )
        )
        provider.add_customer_history(
            CustomerHistory(
                customer_id=scenario.customer_id,
                total_payments=scenario.successful_payments
                + scenario.failed_payments,
                successful_payments=scenario.successful_payments,
                failed_payments=scenario.failed_payments,
            )
        )
        return provider

    def signed_failed_event(self, scenario: ProviderScenario) -> tuple[bytes, str]:
        provider = FakeProvider(webhook_secret=_FAKE_SECRET)
        payload = provider.build_failed_event_payload(
            event_id=scenario.event_id,
            payment_id=scenario.payment_id,
            amount_inr=scenario.amount_inr,
            failure_reason=scenario.failure_reason,
            method=scenario.method,
        )
        return payload, provider.sign(payload)

    def tampered_signature(self, payload: bytes) -> str:
        return "0" * 64


class RazorpayHarness:
    name = "razorpay"

    def _handler(self, scenario: ProviderScenario):
        paise = int(round(scenario.amount_inr * 100))
        base_ts = 1_690_000_000

        payment_entity = {
            "id": scenario.payment_id,
            "entity": "payment",
            "amount": paise,
            "currency": scenario.currency,
            "status": "failed",
            "order_id": scenario.order_id,
            "customer_id": scenario.customer_id,
            "method": "card",
            "error_code": "BAD_REQUEST_ERROR",
            "error_reason": "insufficient_funds",
            "created_at": base_ts,
        }
        order_entity = {
            "id": scenario.order_id,
            "entity": "order",
            "amount": paise,
            "currency": scenario.currency,
            "receipt": "rcpt_1",
            "status": "attempted",
            "created_at": base_ts,
        }
        items = []
        for i in range(scenario.successful_payments):
            items.append(
                {
                    "id": f"pay_c{i}",
                    "status": "captured",
                    "customer_id": scenario.customer_id,
                    "amount": paise,
                    "created_at": base_ts + i,
                }
            )
        for i in range(scenario.failed_payments):
            items.append(
                {
                    "id": f"pay_f{i}",
                    "status": "failed",
                    "customer_id": scenario.customer_id,
                    "amount": paise,
                    "created_at": base_ts + 100 + i,
                }
            )
        # Payments for a different customer, to prove client-side filtering.
        for i in range(2):
            items.append(
                {
                    "id": f"pay_o{i}",
                    "status": "captured",
                    "customer_id": "cust_OTHER",
                    "amount": paise,
                    "created_at": base_ts + i,
                }
            )

        not_found = {"error": {"code": "BAD_REQUEST_ERROR", "description": "no such id"}}

        def handler(request: httpx.Request) -> httpx.Response:
            parts = [p for p in request.url.path.split("/") if p and p != "v1"]
            method = request.method

            if (
                method == "POST"
                and len(parts) == 3
                and parts[0] == "payments"
                and parts[2] == "refund"
            ):
                return httpx.Response(
                    200,
                    json={
                        "id": "rfnd_1",
                        "entity": "refund",
                        "payment_id": parts[1],
                        "amount": paise,
                        "status": "processed",
                        "created_at": base_ts,
                    },
                )
            if method == "POST" and parts == ["payment_links"]:
                return httpx.Response(
                    200,
                    json={
                        "id": "plink_1",
                        "short_url": "https://rzp.io/i/abc123",
                        "amount": paise,
                        "currency": "INR",
                        "status": "created",
                    },
                )
            if method == "GET" and len(parts) == 2 and parts[0] == "payments":
                if parts[1] != scenario.payment_id:
                    return httpx.Response(404, json=not_found)
                return httpx.Response(200, json=payment_entity)
            if method == "GET" and parts == ["payments"]:
                return httpx.Response(
                    200,
                    json={"entity": "collection", "count": len(items), "items": items},
                )
            if method == "GET" and len(parts) == 2 and parts[0] == "orders":
                if parts[1] != scenario.order_id:
                    return httpx.Response(404, json=not_found)
                return httpx.Response(200, json=order_entity)
            if method == "GET" and len(parts) == 2 and parts[0] == "customers":
                if parts[1] != scenario.customer_id:
                    return httpx.Response(404, json=not_found)
                return httpx.Response(
                    200,
                    json={
                        "id": scenario.customer_id,
                        "entity": "customer",
                        "email": "c@example.com",
                    },
                )
            return httpx.Response(404, json=not_found)

        return handler

    def build(self, scenario: ProviderScenario) -> RazorpayAdapter:
        client = httpx.Client(
            base_url=_RZP_BASE, transport=httpx.MockTransport(self._handler(scenario))
        )
        return RazorpayAdapter(
            "rzp_test_x", "secret", _RZP_SECRET, client=client
        )

    def signed_failed_event(self, scenario: ProviderScenario) -> tuple[bytes, str]:
        paise = int(round(scenario.amount_inr * 100))
        body = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": scenario.payment_id,
                        "amount": paise,
                        "currency": scenario.currency,
                        "status": "failed",
                        "order_id": scenario.order_id,
                        "customer_id": scenario.customer_id,
                        "method": "card",
                        "error_reason": "insufficient_funds",
                        "created_at": 1_690_000_000,
                    }
                }
            },
        }
        payload = json.dumps(body).encode("utf-8")
        signature = hmac.new(
            _RZP_SECRET.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        return payload, signature

    def tampered_signature(self, payload: bytes) -> str:
        return "0" * 64


HARNESSES = [FakeHarness(), RazorpayHarness()]


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_provider_conforms(check, harness) -> None:
    check(harness)


def test_scenario_is_the_canonical_one() -> None:
    # Guards against a harness quietly diverging from the shared scenario.
    assert SCENARIO.amount_inr == 2500.0
    assert SCENARIO.successful_payments + SCENARIO.failed_payments == 10
