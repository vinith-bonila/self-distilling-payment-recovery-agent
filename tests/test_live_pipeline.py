"""End-to-end tests of the LIVE application path (not the evaluation harness).

Every test drives the real FastAPI app with a signed webhook and lets the
background task run the real recovery pipeline:

    webhook -> verify -> persist raw -> normalise -> 200
            -> [background] router -> ACTIVE rule | agent -> GuardedExecutor
            -> provider -> trajectory -> ledger

The Fake provider makes the flow deterministic. Starlette's TestClient runs a
request's background tasks before returning, so assertions see the processed
state. Guard state lives in the app's persistent SqlGuardStore.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from _agent_support import ScriptedLLMClient
from fastapi.testclient import TestClient
from test_provider_leakage import _stripe_handler

from agentcore.guardrails import SqlGuardStore
from config import Settings
from llm.stub import StubLLMClient
from providers.fake import FakeProvider
from providers.stripe import StripeProvider
from providers.types import (
    CustomerHistory,
    FailureReason,
    Order,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from recovery.app import create_app
from recovery.db import init_db, session_scope
from recovery.models import InternalEvent, LedgerEntry, RawWebhookEvent
from recovery.rule_schema import payment_rule_schema
from recovery.rules_repo import RuleRepository, RuleStatus
from recovery.trajectory_store import TrajectoryStore

_SECRET = "whsec_fake"
_EFFECTS = ("create_payment_link", "refund_payment")


class CountingLLM:
    """Wraps an LLM client and counts calls, to prove when the LLM is (not) used."""

    def __init__(self, inner=None) -> None:
        self.inner = inner or StubLLMClient()
        self.calls = 0
        self.model = getattr(self.inner, "model", "counting")

    def complete(self, messages, *, temperature: float = 0.0, max_tokens: int = 1024):
        self.calls += 1
        return self.inner.complete(messages, temperature=temperature, max_tokens=max_tokens)


def _provider(*payments: tuple[str, float, FailureReason]) -> FakeProvider:
    """A FakeProvider seeded with payments, with effect calls counted."""
    provider = FakeProvider(webhook_secret=_SECRET)
    for payment_id, amount, reason in payments:
        customer = f"cust_{payment_id}"
        provider.add_payment(
            Payment(
                id=payment_id,
                amount_inr=amount,
                currency="INR",
                status=PaymentStatus.FAILED,
                method=PaymentMethod.CARD,
                order_id=f"order_{payment_id}",
                customer_id=customer,
                failure_reason=reason,
                created_at=datetime.now(timezone.utc),
            )
        )
        provider.add_order(
            Order(id=f"order_{payment_id}", amount_inr=amount, currency="INR", status=OrderStatus.ATTEMPTED)
        )
        provider.add_customer_history(
            CustomerHistory(customer_id=customer, total_payments=4, successful_payments=3, failed_payments=1)
        )
    provider.calls = {name: [] for name in (*_EFFECTS, "get_payment")}  # type: ignore[attr-defined]
    for name in (*_EFFECTS, "get_payment"):
        original = getattr(provider, name)

        def spy(*args, _name=name, _orig=original, **kwargs):
            provider.calls[_name].append(args)  # type: ignore[attr-defined]
            return _orig(*args, **kwargs)

        setattr(provider, name, spy)
    return provider


def _app(tmp_path, provider, *, llm=None, **overrides: Any):
    db_url = f"sqlite:///{(tmp_path / 'live.db').as_posix()}"
    settings = Settings(
        _env_file=None,
        **{
            "approval_threshold_inr": 5000.0,
            "per_run_spend_cap_inr": 100000.0,
            "per_subject_action_budget": 5,
            **overrides,
        },
    )
    app = create_app(
        settings=settings,
        provider_registry={provider.name: provider},
        database_url=db_url,
        llm=llm if llm is not None else CountingLLM(),
    )
    return app, db_url


def _send(client, provider: FakeProvider, payment_id: str, amount: float, reason: FailureReason, event_id: str):
    payload = provider.build_failed_event_payload(
        event_id=event_id, payment_id=payment_id, amount_inr=amount,
        failure_reason=reason, method=PaymentMethod.CARD,
    )
    return client.post(
        "/webhooks/fake",
        content=payload,
        headers={"X-Fake-Signature": provider.sign(payload), "X-Fake-Event-Id": event_id},
    )


def _ledger() -> list[LedgerEntry]:
    with session_scope() as session:
        return session.query(LedgerEntry).order_by(LedgerEntry.id).all()


def _internal() -> list[InternalEvent]:
    with session_scope() as session:
        return session.query(InternalEvent).order_by(InternalEvent.id).all()


def _raw_count() -> int:
    with session_scope() as session:
        return session.query(RawWebhookEvent).count()


def _executed(db_url: str, run_id: str) -> list[str]:
    """Action names the guarded executor actually executed for a run."""
    return [e.action_name for e in SqlGuardStore(db_url).read_audit(run_id) if e.decision == "executed"]


_REFUND_THEN_FINAL = [
    '{"thought":"refund","action":{"tool":"refund_payment","args":{}}}',
    '{"thought":"done","final":{"resolution":"refund"}}',
]


# 1 ---------------------------------------------------------------------------
def test_valid_webhook_reaches_the_router(tmp_path) -> None:
    provider = _provider(("pay_1", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    app, _ = _app(tmp_path, provider)
    with TestClient(app) as client:
        assert _send(client, provider, "pay_1", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_1").status_code == 200
    (event,) = _internal()
    assert event.route == "escalate_to_agent"  # the router ran and found no ACTIVE rule
    (row,) = _ledger()
    assert row.internal_event_id == event.id


# 2 ---------------------------------------------------------------------------
def test_active_rule_handles_matching_case_without_the_llm(tmp_path) -> None:
    provider = _provider(("pay_rb", 1200.0, FailureReason.RISK_BLOCKED), ("pay_id", 800.0, FailureReason.INVALID_DETAILS))
    llm = CountingLLM()
    app, db_url = _app(tmp_path, provider, llm=llm)
    with TestClient(app) as client:  # seed rules are inserted at startup
        _send(client, provider, "pay_rb", 1200.0, FailureReason.RISK_BLOCKED, "evt_rb")
        _send(client, provider, "pay_id", 800.0, FailureReason.INVALID_DETAILS, "evt_id")

    assert llm.calls == 0  # the LLM was never consulted
    escalate, link = _ledger()
    assert (escalate.path, escalate.rule_key, escalate.action) == (
        "deterministic", "seed-risk-blocked-escalate", "escalate_to_human",
    )
    assert escalate.outcome == "escalated" and escalate.trajectory_id is None
    assert (link.path, link.rule_key, link.action, link.outcome) == (
        "deterministic", "seed-invalid-details-resend-link", "send_payment_link", "resolved",
    )
    # The rule path acted through the guarded executor, for the authoritative amount.
    assert _executed(db_url, "pay_rb") == ["escalate_to_human"]
    assert _executed(db_url, "pay_id") == ["send_payment_link"]
    assert provider.calls["create_payment_link"] == [(800.0,)]
    event_rb = _internal()[0]
    assert (event_rb.route, event_rb.matched_rule_key) == ("deterministic", "seed-risk-blocked-escalate")


# 3 ---------------------------------------------------------------------------
def test_unmatched_case_reaches_the_agent(tmp_path) -> None:
    provider = _provider(("pay_a", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    llm = CountingLLM()
    app, _ = _app(tmp_path, provider, llm=llm)
    with TestClient(app) as client:
        _send(client, provider, "pay_a", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_a")
    assert llm.calls > 0
    (row,) = _ledger()
    assert row.path == "llm" and row.rule_key is None and row.trajectory_id is not None


# 4 ---------------------------------------------------------------------------
def test_every_provider_side_effect_goes_through_the_guarded_executor(tmp_path) -> None:
    provider = _provider(
        ("pay_x", 2500.0, FailureReason.INSUFFICIENT_FUNDS),  # agent path
        ("pay_y", 900.0, FailureReason.INVALID_DETAILS),  # rule path
    )
    app, db_url = _app(tmp_path, provider)
    with TestClient(app) as client:
        _send(client, provider, "pay_x", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_x")
        _send(client, provider, "pay_y", 900.0, FailureReason.INVALID_DETAILS, "evt_y")
    effects = len(provider.calls["create_payment_link"]) + len(provider.calls["refund_payment"])
    executed = sum(
        1
        for run in ("pay_x", "pay_y")
        for name in _executed(db_url, run)
        if name in {"send_payment_link", "refund"}
    )
    assert effects == executed == 2  # one-to-one: no provider effect without an executor record


# 5 ---------------------------------------------------------------------------
def test_successful_recovery_is_written_to_trajectory_and_ledger(tmp_path) -> None:
    provider = _provider(("pay_ok", 3100.0, FailureReason.EXPIRED_CARD))
    app, _ = _app(tmp_path, provider)
    with TestClient(app) as client:
        _send(client, provider, "pay_ok", 3100.0, FailureReason.EXPIRED_CARD, "evt_ok")
    (row,) = _ledger()
    assert (row.outcome, row.action, row.execution_status) == ("resolved", "send_payment_link", "executed")
    assert row.prompt_tokens > 0 and row.modelled_cost_usd > 0 and row.latency_ms >= 0
    trajectory = TrajectoryStore().load(row.trajectory_id)
    assert trajectory is not None and trajectory.outcome.value == "resolved"
    assert row.tool_calls == trajectory.iterations
    assert [s.tool for s in trajectory.steps][:3] == ["fetch_payment", "fetch_customer_history", "create_payment_link"]
    assert row.amount_recovered_inr is None  # not known until the customer pays; never guessed


# 6 ---------------------------------------------------------------------------
def test_duplicate_webhook_delivery_executes_once(tmp_path) -> None:
    provider = _provider(("pay_d", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    llm = CountingLLM()
    app, _ = _app(tmp_path, provider, llm=llm)
    with TestClient(app) as client:
        _send(client, provider, "pay_d", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_same")
        calls_after_first = llm.calls
        _send(client, provider, "pay_d", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_same")
    assert _raw_count() == 2  # both deliveries are persisted...
    first, second = _ledger()
    assert second.path == "duplicate_delivery" and second.outcome == "duplicate"
    assert llm.calls == calls_after_first  # ...but the redelivery never reached the LLM
    assert len(provider.calls["create_payment_link"]) == 1  # and never acted twice


def test_distinct_events_for_one_payment_are_idempotent_at_the_executor(tmp_path) -> None:
    provider = _provider(("pay_i", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    app, _ = _app(tmp_path, provider)
    with TestClient(app) as client:
        _send(client, provider, "pay_i", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_i1")
        _send(client, provider, "pay_i", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_i2")
    first, second = _ledger()
    assert first.execution_status == "executed"
    assert second.execution_status == "duplicate"  # persistent idempotency key
    assert len(provider.calls["create_payment_link"]) == 1


# 7 ---------------------------------------------------------------------------
def test_high_value_refund_is_held_for_approval(tmp_path) -> None:
    provider = _provider(("pay_hv", 9000.0, FailureReason.UNKNOWN))
    app, db_url = _app(tmp_path, provider, llm=ScriptedLLMClient(_REFUND_THEN_FINAL))
    with TestClient(app) as client:
        _send(client, provider, "pay_hv", 9000.0, FailureReason.UNKNOWN, "evt_hv")
        dashboard = client.get("/").text
    (row,) = _ledger()
    assert row.outcome == "pending_approval" and row.approval_id
    assert provider.calls["refund_payment"] == []  # nothing refunded
    (pending,) = SqlGuardStore(db_url).list_pending("pay_hv")
    assert pending.cost == 9000.0  # server-derived from the payment record
    assert row.approval_id in dashboard  # surfaced from real persisted state


# 8 ---------------------------------------------------------------------------
def test_shadow_rule_never_executes(tmp_path) -> None:
    provider = _provider(("pay_s", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    app, db_url = _app(tmp_path, provider)
    with TestClient(app) as client:
        RuleRepository(payment_rule_schema()).add_rule(
            "distilled:shadow-escalate",
            {
                "precondition": {"all": [{"field": "failure_reason", "op": "eq", "value": "insufficient_funds"}]},
                "action": {"name": "escalate_to_human", "params": {}},
            },
            status=RuleStatus.SHADOW,
            provenance={"origin": "distilled", "demotion_history": []},
            priority=1,
        )
        _send(client, provider, "pay_s", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_s")
    (row,) = _ledger()
    assert row.path == "llm" and row.rule_key is None  # the shadow rule did not route
    assert "escalate_to_human" not in _executed(db_url, "pay_s")


# 9 ---------------------------------------------------------------------------
def test_spend_cap_blocks_an_excessive_effect(tmp_path) -> None:
    provider = _provider(("pay_sc", 3000.0, FailureReason.UNKNOWN))
    llm = CountingLLM()
    app, _ = _app(
        tmp_path, provider, llm=llm, per_run_spend_cap_inr=1000.0, approval_threshold_inr=100000.0
    )
    with TestClient(app) as client:
        RuleRepository(payment_rule_schema()).add_rule(
            "ops:refund-unknown",
            {
                "precondition": {"all": [{"field": "failure_reason", "op": "eq", "value": "unknown"}]},
                "action": {"name": "refund", "params": {"full": True}},
            },
            status=RuleStatus.ACTIVE,
            priority=1,
        )
        _send(client, provider, "pay_sc", 3000.0, FailureReason.UNKNOWN, "evt_sc")
    (row,) = _ledger()
    assert (row.path, row.execution_status, row.outcome) == ("deterministic", "rejected_spend_cap", "rejected")
    assert provider.calls["refund_payment"] == [] and llm.calls == 0


# 10 --------------------------------------------------------------------------
def test_malformed_and_unknown_tool_calls_fail_safely(tmp_path) -> None:
    provider = _provider(("pay_m", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    llm = ScriptedLLMClient(['{"thought":"x","action":{"tool":"delete_database","args":{}}}', "not json {{"])
    app, _ = _app(tmp_path, provider, llm=llm)
    with TestClient(app) as client:
        assert _send(client, provider, "pay_m", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_m").status_code == 200
    (row,) = _ledger()
    assert row.outcome == "failed_malformed_output"
    assert all(calls == [] for name, calls in provider.calls.items() if name in _EFFECTS)
    trajectory = TrajectoryStore().load(row.trajectory_id)
    assert [s.decision for s in trajectory.steps] == ["rejected_unknown_tool", "malformed_output"]


# 11 --------------------------------------------------------------------------
def test_invalid_signature_never_reaches_the_router(tmp_path) -> None:
    provider = _provider(("pay_bad", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    llm = CountingLLM()
    app, _ = _app(tmp_path, provider, llm=llm)
    payload = provider.build_failed_event_payload(
        event_id="evt_bad", payment_id="pay_bad", amount_inr=2500.0,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS, method=PaymentMethod.CARD,
    )
    with TestClient(app) as client:
        response = client.post("/webhooks/fake", content=payload, headers={"X-Fake-Signature": "0" * 64})
    assert response.status_code == 400
    assert (_raw_count(), len(_internal()), len(_ledger())) == (0, 0, 0)
    assert llm.calls == 0 and provider.calls["get_payment"] == []


# 12 --------------------------------------------------------------------------
def test_raw_webhook_is_persisted_before_processing(tmp_path) -> None:
    provider = _provider(("pay_o", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    app, _ = _app(tmp_path, provider)
    seen: list[tuple[int, int]] = []
    with TestClient(app) as client:
        original = app.state.pipeline.process

        def observed(internal_event_id: int):
            seen.append((_raw_count(), len(_internal())))  # state when processing starts
            return original(internal_event_id)

        app.state.pipeline.process = observed
        _send(client, provider, "pay_o", 2500.0, FailureReason.INSUFFICIENT_FUNDS, "evt_o")
    assert seen == [(1, 1)]


def test_processing_failure_keeps_the_raw_event_and_the_200(tmp_path) -> None:
    provider = _provider()  # the webhook names a payment the provider does not have
    app, _ = _app(tmp_path, provider)
    with TestClient(app) as client:
        response = _send(client, provider, "pay_missing", 500.0, FailureReason.CARD_DECLINED, "evt_missing")
    assert response.status_code == 200
    assert _raw_count() == 1 and len(_internal()) == 1
    (row,) = _ledger()
    assert row.path == "failed" and row.outcome == "failed" and "ResourceNotFound" in row.error
    assert all(calls == [] for name, calls in provider.calls.items() if name in _EFFECTS)


# 13 --------------------------------------------------------------------------
def test_provider_specific_data_does_not_leak_above_the_adapter(tmp_path) -> None:
    stripe = StripeProvider(
        "sk_test_x",
        "whsec_stripe",
        client=httpx.Client(base_url="https://api.stripe.com/v1", transport=httpx.MockTransport(_stripe_handler)),
    )
    db_url = f"sqlite:///{(tmp_path / 'stripe.db').as_posix()}"
    app = create_app(
        settings=Settings(_env_file=None),
        provider_registry={"stripe": stripe},
        database_url=db_url,
        llm=CountingLLM(),
    )
    body = {
        "id": "evt_stripe_1",
        "type": "payment_intent.payment_failed",
        "created": 1_690_000_000,
        "data": {"object": {"id": "pi_1", "amount": 250000, "currency": "inr",
                            "status": "requires_payment_method", "customer": "cus_1",
                            "last_payment_error": {"decline_code": "insufficient_funds"}}},
    }
    payload = json.dumps(body).encode("utf-8")
    digest = hmac.new(b"whsec_stripe", b"1690000000." + payload, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": f"t=1690000000,v1={digest}"})

    (row,) = _ledger()
    # Normalised values only: rupees (not minor units) and the shared enum value,
    # produced by the same pipeline with no provider-specific branch.
    assert (row.provider, row.failure_reason, row.amount_inr) == ("stripe", "insufficient_funds", 2500.0)
    assert (row.path, row.outcome, row.action) == ("llm", "resolved", "send_payment_link")
    fetched = next(s for s in TrajectoryStore().load(row.trajectory_id).steps if s.tool == "fetch_payment")
    assert fetched.observation["amount_inr"] == 2500.0
    assert fetched.observation["status"] == "failed"
    raw_observations = json.dumps([s.observation for s in TrajectoryStore().load(row.trajectory_id).steps])
    for stripe_only in ("requires_payment_method", "decline_code", "last_payment_error", "250000"):
        assert stripe_only not in raw_observations


# crash recovery --------------------------------------------------------------
def test_startup_drains_events_left_unprocessed_by_a_crash(tmp_path) -> None:
    provider = _provider(("pay_c", 2500.0, FailureReason.INSUFFICIENT_FUNDS))
    db_url = f"sqlite:///{(tmp_path / 'live.db').as_posix()}"
    # Simulate: the webhook persisted the event and returned 200, then the
    # process died before the background task ran.
    init_db(db_url)
    with session_scope() as session:
        session.add(InternalEvent(
            provider="fake", event_id="evt_c", event_type="payment_failed", payment_id="pay_c",
            amount_inr=2500.0, failure_reason="insufficient_funds", occurred_at=None,
        ))
    app, _ = _app(tmp_path, provider)
    with TestClient(app):
        pass  # lifespan drain runs here
    (row,) = _ledger()
    assert row.outcome == "resolved" and len(provider.calls["create_payment_link"]) == 1

    app_again, _ = _app(tmp_path, provider)
    with TestClient(app_again):
        pass  # a second restart must not re-process it
    assert len(_ledger()) == 1 and len(provider.calls["create_payment_link"]) == 1


# llm backend -----------------------------------------------------------------
def test_live_llm_defaults_to_the_frozen_stub_and_groq_needs_a_key() -> None:
    import pytest
    from pydantic import ValidationError

    from recovery.llm_factory import build_llm

    assert isinstance(build_llm(Settings(_env_file=None)), StubLLMClient)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_backend="groq")  # refuses to boot without a key
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_backend="something-else")


# razorpay path (the manual TEST-mode procedure's exact payload) -------------
def _manual_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "send_razorpay_test_webhook.py"
    spec = importlib.util.spec_from_file_location("send_razorpay_test_webhook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _razorpay_api(request: httpx.Request) -> httpx.Response:
    """Mocked Razorpay TEST API (no network): the calls the live pipeline makes."""
    parts = [p for p in request.url.path.split("/") if p and p != "v1"]
    if request.method == "GET" and parts == ["payments", "pay_rzp1"]:
        return httpx.Response(200, json={
            "id": "pay_rzp1", "entity": "payment", "amount": 49900, "currency": "INR",
            "status": "failed", "method": "card", "customer_id": "cust_rzp1",
            "error_reason": "insufficient_funds", "created_at": 1_690_000_000,
        })
    if request.method == "GET" and parts == ["customers", "cust_rzp1"]:
        return httpx.Response(200, json={"id": "cust_rzp1", "entity": "customer"})
    if request.method == "GET" and parts == ["payments"]:
        return httpx.Response(200, json={"entity": "collection", "items": []})
    if request.method == "POST" and parts == ["payment_links"]:
        return httpx.Response(200, json={
            "id": "plink_rzp1", "short_url": "https://rzp.io/i/test", "amount": 49900, "status": "created",
        })
    return httpx.Response(404, json={"error": {"code": "BAD_REQUEST_ERROR"}})


def test_razorpay_webhook_from_the_manual_procedure_runs_the_live_pipeline(tmp_path) -> None:
    from providers.razorpay import RazorpayAdapter

    script = _manual_script()
    created: list[bytes] = []

    def api(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            created.append(request.content)
        return _razorpay_api(request)

    adapter = RazorpayAdapter(
        "rzp_test_manual", "secret", "whsec_rzp_manual",
        client=httpx.Client(base_url="https://api.razorpay.com/v1", transport=httpx.MockTransport(api)),
    )
    app = create_app(
        settings=Settings(_env_file=None),
        provider_registry={"razorpay": adapter},
        database_url=f"sqlite:///{(tmp_path / 'rzp.db').as_posix()}",
        llm=CountingLLM(),
    )
    body = script.build_payload("pay_rzp1", 499.0, "insufficient_funds", customer_id="cust_rzp1")
    with TestClient(app) as client:
        bad = client.post("/webhooks/razorpay", content=body,
                          headers={"X-Razorpay-Signature": "0" * 64, "X-Razorpay-Event-Id": "evt_rzp_bad"})
        ok = client.post("/webhooks/razorpay", content=body,
                         headers={"X-Razorpay-Signature": script.sign("whsec_rzp_manual", body),
                                  "X-Razorpay-Event-Id": "evt_rzp_1"})
    assert (bad.status_code, ok.status_code) == (400, 200)
    (event,) = _internal()
    assert (event.provider, event.failure_reason, event.amount_inr) == ("razorpay", "insufficient_funds", 499.0)
    (row,) = _ledger()
    assert (row.path, row.outcome, row.action, row.execution_status) == (
        "llm", "resolved", "send_payment_link", "executed",
    )
    assert len(created) == 1  # exactly one payment link requested from the TEST API
