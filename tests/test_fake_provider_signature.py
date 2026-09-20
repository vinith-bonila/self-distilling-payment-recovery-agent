"""The fake provider's HMAC signature contract (foundation for Phase 3)."""
from __future__ import annotations

from providers.fake import FakeProvider


def test_valid_signature_verifies() -> None:
    provider = FakeProvider(webhook_secret="whsec_test")
    payload = b'{"event":"payment.failed"}'
    assert provider.verify_signature(payload, provider.sign(payload)) is True


def test_tampered_payload_fails() -> None:
    provider = FakeProvider(webhook_secret="whsec_test")
    signature = provider.sign(b'{"event":"payment.failed"}')
    assert provider.verify_signature(b'{"event":"payment.captured"}', signature) is False


def test_wrong_secret_fails() -> None:
    signer = FakeProvider(webhook_secret="whsec_test")
    verifier = FakeProvider(webhook_secret="whsec_other")
    payload = b'{"event":"payment.failed"}'
    assert verifier.verify_signature(payload, signer.sign(payload)) is False
