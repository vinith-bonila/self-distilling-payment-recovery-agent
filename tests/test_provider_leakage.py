"""The abstraction test: providers stay inside providers/.

Proves that no provider name, API string or adapter type appears above the
adapter layer, and that the identical recovery agent + guardrails + trajectory
machinery run against Stripe with no provider-specific branching.
"""
from __future__ import annotations

import pathlib

import httpx

from _agent_support import settings
from _ast_imports import REPO_ROOT

from agentcore.trajectory import AgentOutcome
from llm.stub import StubLLMClient
from providers.stripe import StripeProvider
from recovery.agent import run_recovery
from recovery.tools import RecoveryCase

PROVIDER_NAMES = ("stripe", "razorpay")
# The single legitimate wiring point where concrete adapters are registered.
REGISTRY = "providers_registry.py"


def _sources(package: str) -> list[pathlib.Path]:
    return sorted((REPO_ROOT / package).rglob("*.py"))


def test_agentcore_never_mentions_a_payment_provider() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _sources("agentcore"):
        text = path.read_text(encoding="utf-8").lower()
        hits = [name for name in PROVIDER_NAMES if name in text]
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert not offenders, f"agentcore must be provider-agnostic: {offenders}"


def test_recovery_mentions_providers_only_in_the_registry() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _sources("recovery"):
        if path.name == REGISTRY:
            continue  # registration, not branching
        text = path.read_text(encoding="utf-8").lower()
        hits = [name for name in PROVIDER_NAMES if name in text]
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert not offenders, (
        "recovery must not reference a concrete provider outside the registry: "
        f"{offenders}"
    )


def test_registry_has_no_per_provider_branching() -> None:
    # The registry is a flat mapping; adding a provider must not add control flow.
    text = (REPO_ROOT / "recovery" / REGISTRY).read_text(encoding="utf-8")
    for keyword in ("if ", "elif ", "match "):
        assert keyword not in text, f"registry should not branch on provider ({keyword!r})"


# --- the same agent, against Stripe --------------------------------------


def _stripe_handler(request: httpx.Request) -> httpx.Response:
    parts = [p for p in request.url.path.split("/") if p and p != "v1"]
    intent = {
        "id": "pi_1",
        "amount": 250000,
        "currency": "inr",
        "status": "requires_payment_method",
        "customer": "cus_1",
        "payment_method_types": ["card"],
        "metadata": {"order_id": "cs_1"},
        "last_payment_error": {"decline_code": "insufficient_funds"},
        "created": 1_690_000_000,
    }
    if request.method == "GET" and parts == ["payment_intents", "pi_1"]:
        return httpx.Response(200, json=intent)
    if request.method == "GET" and parts == ["payment_intents"]:
        return httpx.Response(
            200,
            json={"data": [{"id": "pi_x", "status": "succeeded", "amount": 100, "created": 1}]},
        )
    if request.method == "GET" and parts == ["customers", "cus_1"]:
        return httpx.Response(200, json={"id": "cus_1"})
    if request.method == "POST" and parts == ["checkout", "sessions"]:
        return httpx.Response(
            200,
            json={
                "id": "cs_test_1",
                "url": "https://checkout.stripe.com/c/pay/cs_test_1",
                "amount_total": 250000,
                "currency": "inr",
                "status": "open",
            },
        )
    return httpx.Response(404, json={"error": {"code": "resource_missing"}})


def test_same_agent_and_guardrails_run_against_stripe() -> None:
    provider = StripeProvider(
        "sk_test_x",
        "whsec_stripe",
        client=httpx.Client(
            base_url="https://api.stripe.com/v1",
            transport=httpx.MockTransport(_stripe_handler),
        ),
    )
    case = RecoveryCase(
        payment_id="pi_1",
        amount_inr=2500.0,
        failure_reason="insufficient_funds",
        method="card",
        customer_id="cus_1",
        order_id="cs_1",
    )
    trajectory = run_recovery(case, provider, StubLLMClient(), settings())

    # Identical behaviour to the fake/razorpay path: no branching anywhere.
    assert trajectory.outcome is AgentOutcome.RESOLVED
    assert trajectory.resolution_action == "send_payment_link"
    assert any(s.decision == "effect_executed" for s in trajectory.steps)
    # Observations returned to the model are normalised, not Stripe-shaped.
    fetched = next(s for s in trajectory.steps if s.tool == "fetch_payment")
    assert fetched.observation["failure_reason"] == "insufficient_funds"
    assert fetched.observation["amount_inr"] == 2500.0
    assert fetched.observation["status"] == "failed"
