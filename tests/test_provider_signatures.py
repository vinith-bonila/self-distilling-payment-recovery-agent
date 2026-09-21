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
from providers.stripe import StripeProvider

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


def _offline_stripe(webhook_secret: str = "whsec_stripe") -> StripeProvider:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        base_url="https://api.stripe.com/v1",
    )
    return StripeProvider("sk_test_x", webhook_secret, client=client)


def _stripe_sig(secret: str, payload: bytes, timestamp: str = "1690000000") -> str:
    signed = f"{timestamp}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_stripe_signature_valid_tampered_and_wrong_secret() -> None:
    provider = _offline_stripe("whsec_stripe")
    assert provider.verify_signature(PAYLOAD, _stripe_sig("whsec_stripe", PAYLOAD)) is True
    assert provider.verify_signature(PAYLOAD, "t=1690000000,v1=" + "0" * 64) is False
    assert provider.verify_signature(PAYLOAD, _stripe_sig("whsec_wrong", PAYLOAD)) is False


def test_stripe_signature_rejects_malformed_header() -> None:
    provider = _offline_stripe()
    for bad in ("", "garbage", "v1=abc", "t=1690000000", "t=1,v0=abc"):
        assert provider.verify_signature(PAYLOAD, bad) is False


def test_stripe_signature_is_bound_to_timestamp() -> None:
    # A signature computed for one timestamp must not verify under another.
    provider = _offline_stripe("whsec_stripe")
    signature = _stripe_sig("whsec_stripe", PAYLOAD, timestamp="1690000000")
    swapped = signature.replace("t=1690000000", "t=1690009999")
    assert provider.verify_signature(PAYLOAD, swapped) is False


def test_stripe_adapter_refuses_live_key() -> None:
    with pytest.raises(ValueError):
        StripeProvider("sk_live_danger", "whsec_stripe")
