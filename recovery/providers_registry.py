"""Build the map of provider name -> adapter from settings.

Adapters are constructed with sandbox credentials only (enforced by config and,
for Razorpay, by the adapter itself). The webhook layer looks providers up here
by name; it never references a concrete adapter type.
"""
from __future__ import annotations

import hashlib
import os

from config import Settings
from providers.base import PaymentProvider
from providers.fake import FakeProvider
from providers.razorpay import RazorpayAdapter
from providers.stripe import StripeProvider


def _print_webhook_secret_fingerprint() -> None:
    """TEMPORARY startup diagnostic — remove before the final submission.

    Prints whether RAZORPAY_WEBHOOK_SECRET is present in this process, its
    length and its SHA-256 fingerprint. Never the secret itself. Printed to
    stdout (flushed) so it reaches the platform logs regardless of logging
    configuration.
    """
    value = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    present = str(value is not None).lower()
    raw = (value or "").encode("utf-8")
    print(
        f"Razorpay webhook secret fingerprint: present={present} "
        f"length={len(value or '')} sha256={hashlib.sha256(raw).hexdigest()}",
        flush=True,
    )


def build_registry(settings: Settings) -> dict[str, PaymentProvider]:
    """Return {provider_name: adapter} for every configured provider.

    Adding a provider is a registration, not a branch: nothing above this map
    knows which adapter it is talking to.
    """
    _print_webhook_secret_fingerprint()  # TEMPORARY: remove before final submission
    return {
        FakeProvider.name: FakeProvider(webhook_secret=settings.fake_webhook_secret),
        RazorpayAdapter.name: RazorpayAdapter(
            settings.razorpay_key_id,
            settings.razorpay_key_secret,
            settings.razorpay_webhook_secret,
        ),
        StripeProvider.name: StripeProvider(
            settings.stripe_api_key,
            settings.stripe_webhook_secret,
        ),
    }
