"""The sandbox-key boot assertion."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import Settings


def test_empty_keys_boot() -> None:
    settings = Settings(_env_file=None)
    assert settings.razorpay_key_id == ""
    assert settings.stripe_api_key == ""


def test_valid_sandbox_keys_boot() -> None:
    settings = Settings(
        _env_file=None,
        razorpay_key_id="rzp_test_abc123",
        stripe_api_key="sk_test_abc123",
    )
    assert settings.razorpay_key_id == "rzp_test_abc123"
    assert settings.stripe_api_key == "sk_test_abc123"


def test_live_razorpay_key_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, razorpay_key_id="rzp_live_abc123")


def test_live_stripe_key_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stripe_api_key="sk_live_abc123")
