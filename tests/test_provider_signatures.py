"""Webhook signature verification, per provider.

Each adapter must accept a valid signature and reject a tampered one and one
made with the wrong secret. The Razorpay adapter must also refuse to construct
with a non-sandbox key.
"""
from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from providers.fake import FakeProvider
from providers.razorpay import RazorpayAdapter

PAYLOAD = b'{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_1"}}}}'


def _offline_razorpay(webhook_secret: str = "whsec_rzp") -> RazorpayAdapter:
    # A MockTransport that fails any accidental network call keeps this test
    # honest: signature verification must not touch the network.
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        base_url="https://api.razorpay.com/v1",
    )
    return RazorpayAdapter("rzp_test_x", "secret", webhook_secret, client=client)


def test_fake_signature_valid_tampered_and_wrong_secret() -> None:
    provider = FakeProvider(webhook_secret="whsec_fake")
    assert provider.verify_signature(PAYLOAD, provider.sign(PAYLOAD)) is True
    assert provider.verify_signature(PAYLOAD, "0" * 64) is False
    other = FakeProvider(webhook_secret="whsec_other")
    assert provider.verify_signature(PAYLOAD, other.sign(PAYLOAD)) is False


def test_razorpay_signature_valid_tampered_and_wrong_secret() -> None:
    provider = _offline_razorpay("whsec_rzp")
    valid = hmac.new(b"whsec_rzp", PAYLOAD, hashlib.sha256).hexdigest()
    wrong = hmac.new(b"whsec_wrong", PAYLOAD, hashlib.sha256).hexdigest()
    assert provider.verify_signature(PAYLOAD, valid) is True
    assert provider.verify_signature(PAYLOAD, "0" * 64) is False
    assert provider.verify_signature(PAYLOAD, wrong) is False


def test_razorpay_adapter_refuses_live_key() -> None:
    with pytest.raises(ValueError):
        RazorpayAdapter("rzp_live_danger", "secret", "whsec_rzp")
