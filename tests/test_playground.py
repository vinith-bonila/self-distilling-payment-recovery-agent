"""The Agent Playground drives the LIVE pipeline and reports only what it did.

``POST /playground/api/run`` must be a thin adapter: it seeds a synthetic
payment in the in-memory demo provider, writes a ``demo`` internal event and
calls ``RecoveryPipeline.process`` — the same router, agent, guarded executor,
trajectory store and ledger as the webhook path. These tests check that the
response is exactly what the pipeline persisted, that nothing about a demo run
can pass for a real recovery, and that the webhook path is untouched.
"""
from __future__ import annotations

from typing import Any

import pytest
from _agent_support import ScriptedLLMClient
from fastapi.testclient import TestClient
from test_live_pipeline import CountingLLM, _app, _ledger, _provider, _send

from agentcore.guardrails import SqlGuardStore
from providers.types import FailureReason
from recovery.ledger import LedgerRepository
from recovery.models import InternalEvent, RawWebhookEvent
from recovery.db import session_scope
from recovery.trajectory_store import TrajectoryStore


def _run(client: TestClient, **overrides: Any):
    body = {
        "payment_id": "pay_demo_001",
        "amount_inr": 1000,
        "failure_reason": "insufficient_funds",
        **overrides,
    }
    return client.post("/playground/api/run", json=body)


# 1 ---------------------------------------------------------------------------
def test_known_rule_takes_the_deterministic_path_without_the_llm(tmp_path) -> None:
    llm = CountingLLM()
    app, db_url = _app(tmp_path, _provider(), llm=llm)
    with TestClient(app) as client:
        r = _run(client, failure_reason="invalid_details").json()

    assert llm.calls == 0
    assert r["path"] == "deterministic"
    assert r["rule"] == {"key": "seed-invalid-details-resend-link", "version": 1}
    assert r["action"] == "send_payment_link"
    assert r["llm"]["calls"] == 0 and r["trajectory"] is None
    assert r["outcome"] == "resolved"


# 2 ---------------------------------------------------------------------------
def test_unknown_case_takes_the_llm_path_and_reports_real_call_count(tmp_path) -> None:
    llm = CountingLLM()
    app, _ = _app(tmp_path, _provider(), llm=llm)
    with TestClient(app) as client:
        r = _run(client, failure_reason="insufficient_funds").json()

    assert r["path"] == "llm" and r["rule"] is None
    assert llm.calls > 0
    assert r["llm"]["calls"] == llm.calls  # the count shown is the count made
    persisted = TrajectoryStore().load(r["trajectory"]["id"])
    assert persisted is not None
    assert [s["decision"] for s in r["trajectory"]["steps"]] == [s.decision for s in persisted.steps]


# 3 ---------------------------------------------------------------------------
@pytest.mark.parametrize("reason", ["invalid_details", "risk_blocked", "insufficient_funds"])
def test_every_path_acts_through_the_guarded_executor(tmp_path, reason) -> None:
    app, db_url = _app(tmp_path, _provider())
    with TestClient(app) as client:
        r = _run(client, failure_reason=reason).json()

    audit = SqlGuardStore(db_url).read_audit("pay_demo_001")
    assert audit, "the executor's audit log is the only record of an effect"
    assert [(a["action"], a["decision"]) for a in r["executor"]["audit"]] == [
        (a.action_name, a.decision) for a in audit
    ]
    assert r["executor"]["status"] == audit[-1].decision


def test_refund_above_threshold_is_held_for_approval_not_executed(tmp_path) -> None:
    llm = ScriptedLLMClient([
        '{"thought":"refund","action":{"tool":"refund_payment","args":{}}}',
        '{"thought":"done","final":{"resolution":"refund"}}',
    ])
    app, db_url = _app(tmp_path, _provider(), llm=llm)
    with TestClient(app) as client:
        r = _run(client, amount_inr=50000).json()
    demo = app.state.demo_provider

    assert r["executor"]["status"] == "pending_approval"
    assert r["outcome"] == "pending_approval" and r["executor"]["approval_id"]
    assert not [a for a in SqlGuardStore(db_url).read_audit("pay_demo_001") if a.decision == "executed"]
    assert demo._refund_seq == 0  # no refund was issued, even to the demo provider


# 4 ---------------------------------------------------------------------------
def test_response_is_exactly_the_persisted_ledger_row(tmp_path) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        r = _run(client, failure_reason="risk_blocked").json()

    row = LedgerRepository().get(r["ledger_id"])
    assert row is not None
    assert (r["path"], r["action"], r["outcome"], r["executor"]["status"]) == (
        row.path, row.action, row.outcome, row.execution_status,
    )
    assert (r["event"]["payment_id"], r["event"]["amount_inr"], r["event"]["failure_reason"]) == (
        "pay_demo_001", 1000.0, "risk_blocked",
    )
    assert r["outcome"] == "escalated"


def test_repeat_run_reports_the_executors_idempotent_replay(tmp_path) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        first = _run(client, failure_reason="invalid_details").json()
        second = _run(client, failure_reason="invalid_details").json()

    assert first["executor"]["status"] == "executed"
    assert second["executor"]["status"] == "duplicate"
    assert second["outcome"] == "duplicate"


# 5 ---------------------------------------------------------------------------
def test_no_provider_or_llm_secret_is_exposed(tmp_path) -> None:
    secrets = {
        "razorpay_key_id": "rzp_test_SECRETKEYID1",
        "razorpay_key_secret": "rzp_secret_value_do_not_leak",
        "razorpay_webhook_secret": "whsec_razorpay_do_not_leak",
        "stripe_webhook_secret": "whsec_stripe_do_not_leak",
        "fake_webhook_secret": "whsec_fake_do_not_leak",
        "groq_api_key": "gsk_do_not_leak",
    }
    app, _ = _app(tmp_path, _provider(), **secrets)
    with TestClient(app) as client:
        bodies = [
            client.get("/playground").text,
            client.get("/playground/api/overview").text,
            _run(client).text,
            _run(client, payment_id="pay_demo_002", failure_reason="invalid_details").text,
        ]
    for body in bodies:
        for value in secrets.values():
            assert value not in body


# 6 ---------------------------------------------------------------------------
def test_demo_result_is_labelled_simulation_and_never_a_recovery(tmp_path) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        r = _run(client, failure_reason="invalid_details").json()
        page = client.get("/playground").text

    assert r["mode"] == "simulation" and r["provider"] == "demo"
    assert r["money_moved"] is False
    assert r["amount_recovered_inr"] is None
    assert "No real customer money was moved or recovered" in r["disclaimer"]
    assert "SIMULATION / TEST MODE" in page
    assert "No real customer money was moved or recovered by this demo." in page


@pytest.mark.parametrize(
    "overrides",
    [
        {"payment_id": "pay_NqRealRazorpay1"},  # a real-looking provider id
        {"payment_id": "pay_demo_x; drop"},
        {"customer_id": "cust_real_42"},
        {"amount_inr": 0},
        {"amount_inr": 5_000_000},
        {"failure_reason": "not_a_reason"},
        {"action": "refund"},  # the client never chooses the action
        {"refund_amount_inr": 1},  # nor a refund amount
    ],
)
def test_input_cannot_leave_the_demo_namespace_or_steer_the_decision(tmp_path, overrides) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        assert _run(client, **overrides).status_code == 422
    assert _ledger() == []


def test_playground_is_rate_limited(tmp_path) -> None:
    from recovery.playground import RateLimiter

    app, _ = _app(tmp_path, _provider())
    app.state.playground_limiter = RateLimiter(max_runs=2, window_seconds=60)
    with TestClient(app) as client:
        codes = [_run(client, payment_id=f"pay_demo_{i}").status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_overview_reports_live_state_and_labels_evaluation_offline(tmp_path) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        o = client.get("/playground/api/overview").json()

    assert o["system"] == {"online": True, "mode": "simulation"}
    assert {r["rule_key"] for r in o["rules"] if r["status"] == "active"} == {
        "seed-risk-blocked-escalate", "seed-invalid-details-resend-link",
    }
    assert o["lifecycle"]["live_auto_distillation"] is False
    assert o["evaluation"]["label"].startswith("Offline evaluation")


# 7 ---------------------------------------------------------------------------
def test_demo_provider_is_not_a_webhook_endpoint(tmp_path) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        resp = client.post("/webhooks/demo", content=b"{}", headers={"X-Fake-Signature": "x"})
        assert resp.status_code == 404
    with session_scope() as session:
        assert session.query(RawWebhookEvent).count() == 0
        assert session.query(InternalEvent).count() == 0


def test_webhook_path_is_unchanged_alongside_playground_runs(tmp_path) -> None:
    provider = _provider(("pay_w", 800.0, FailureReason.INVALID_DETAILS))
    app, _ = _app(tmp_path, provider)
    with TestClient(app) as client:
        _run(client, failure_reason="invalid_details")
        assert _send(client, provider, "pay_w", 800.0, FailureReason.INVALID_DETAILS, "evt_w").status_code == 200
        dashboard = client.get("/").text

    demo_row, webhook_row = _ledger()
    assert (demo_row.provider, webhook_row.provider) == ("demo", "fake")
    assert (webhook_row.path, webhook_row.outcome) == ("deterministic", "resolved")
    # the real provider was used for the real event, the demo provider for the demo
    assert provider.calls["create_payment_link"] == [(800.0,)]
    assert "1 normalised webhook event(s) received" in dashboard  # demo run is not a webhook


def test_activity_lists_only_real_demo_runs_as_recorded(tmp_path) -> None:
    provider = _provider(("pay_w2", 800.0, FailureReason.INVALID_DETAILS))
    app, _ = _app(tmp_path, provider)
    with TestClient(app) as client:
        assert client.get("/playground/api/activity").json() == {"mode": "simulation", "runs": []}
        rule = _run(client, payment_id="pay_demo_a", failure_reason="invalid_details").json()
        llm = _run(client, payment_id="pay_demo_b", failure_reason="insufficient_funds").json()
        _send(client, provider, "pay_w2", 800.0, FailureReason.INVALID_DETAILS, "evt_w2")  # a webhook row
        runs = client.get("/playground/api/activity").json()["runs"]

    assert [r["ledger_id"] for r in runs] == [llm["ledger_id"], rule["ledger_id"]]  # newest first, no webhook row
    newest, oldest = runs
    assert (newest["path"], newest["llm_calls"], newest["outcome"]) == ("llm", llm["llm"]["calls"], llm["outcome"])
    assert (oldest["path"], oldest["llm_calls"], oldest["execution_status"]) == ("deterministic", 0, "executed")
    assert all(r["recorded_at"].endswith("+00:00") for r in runs)


def test_overview_failure_reasons_are_the_backend_enum(tmp_path) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        reasons = client.get("/playground/api/overview").json()["failure_reasons"]
    assert reasons == [r.value for r in FailureReason]


def test_razorpay_integration_path_is_labelled_future_and_never_claims_live(tmp_path) -> None:
    app, _ = _app(tmp_path, _provider())
    with TestClient(app) as client:
        page = client.get("/playground").text

    for label in ("Razorpay integration ready", "TEST MODE • NO REAL MONEY", "FUTURE INTEGRATION PATH",
                  "FUTURE • NOT ENABLED IN THIS DEMO", "NOT YET RUN LIVE", "How would we connect Razorpay?"):
        assert label in page
    lowered = page.lower()
    for claim in ("razorpay connected", "live razorpay connected", "production ready", "production-ready",
                  "recovering real payments", "live payments are being monitored"):
        assert claim not in lowered
