"""In-memory fake provider for offline tests and the eval harness.

Signatures are real HMAC-SHA256 over the raw body with a shared secret, so the
signature-verification tests exercise genuine crypto rather than a stub.
"""
from __future__ import annotations

import hashlib
import hmac


class FakeProvider:
    """A deterministic, network-free payment provider.

    Phase 1 implements the signature contract; recovery tools, event parsing and
    the ledger-facing surface are added in Phases 3-6.
    """

    name = "fake"

    def __init__(self, webhook_secret: str = "whsec_fake") -> None:
        self._secret = webhook_secret.encode("utf-8")

    def sign(self, payload: bytes) -> str:
        """Produce a valid signature for ``payload`` (test/eval helper)."""
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Constant-time compare of the expected signature against ``signature``."""
        expected = self.sign(payload)
        return hmac.compare_digest(expected, signature)
