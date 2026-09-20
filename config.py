"""Application settings and the sandbox-key boot assertion.

This module is imported by ``recovery``, ``llm`` and ``evals`` but never by
``agentcore`` (which must stay domain- and vendor-neutral). Settings are loaded
from the environment and an optional ``.env`` file.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration.

    Sandbox-only guarantee: the app refuses to construct settings if a provider
    key is present but is not a test-mode key (see :meth:`_enforce_sandbox_keys`).
    An empty key is allowed so fully offline runs need no credentials at all.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Storage. Postgres-ready: change the URL, nothing else.
    database_url: str = "sqlite:///./payment_recovery.db"

    # Payment provider credentials — SANDBOX ONLY.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = "whsec_fake_razorpay"
    stripe_api_key: str = ""
    stripe_webhook_secret: str = "whsec_fake_stripe"

    # Fake provider webhook secret (offline runs and tests).
    fake_webhook_secret: str = "whsec_fake"

    # LLM (Groq). An empty key is fine offline: the stub client needs no network.
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Guardrail defaults (money amounts in INR / rupees).
    approval_threshold_inr: float = 5000.0
    per_run_spend_cap_inr: float = 100000.0
    per_subject_action_budget: int = 5
    agent_max_iterations: int = 5
    agent_timeout_seconds: float = 30.0

    @model_validator(mode="after")
    def _enforce_sandbox_keys(self) -> "Settings":
        """Refuse to boot with non-sandbox provider keys."""
        if self.razorpay_key_id and not self.razorpay_key_id.startswith("rzp_test_"):
            raise ValueError(
                "Refusing to boot: RAZORPAY_KEY_ID must be a sandbox key (rzp_test_...)."
            )
        if self.stripe_api_key and not self.stripe_api_key.startswith("sk_test_"):
            raise ValueError(
                "Refusing to boot: STRIPE_API_KEY must be a sandbox key (sk_test_...)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (validated on first call)."""
    return Settings()
