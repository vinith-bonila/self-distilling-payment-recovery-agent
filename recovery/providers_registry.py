"""Build the map of provider name -> adapter from settings.

Adapters are constructed with sandbox credentials only (enforced by config and,
for Razorpay, by the adapter itself). The webhook layer looks providers up here
by name; it never references a concrete adapter type.
"""
from __future__ import annotations

from config import Settings
from providers.base import PaymentProvider
from providers.fake import FakeProvider
from providers.razorpay import RazorpayAdapter


def build_registry(settings: Settings) -> dict[str, PaymentProvider]:
    """Return {provider_name: adapter} for every configured provider."""
    return {
        FakeProvider.name: FakeProvider(webhook_secret=settings.fake_webhook_secret),
        RazorpayAdapter.name: RazorpayAdapter(
            settings.razorpay_key_id,
            settings.razorpay_key_secret,
            settings.razorpay_webhook_secret,
        ),
    }
