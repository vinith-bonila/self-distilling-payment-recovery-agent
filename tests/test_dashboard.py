"""The minimal dashboard: renders, stays read-only, escapes untrusted content."""
from __future__ import annotations

import json
import shutil

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from providers.fake import FakeProvider
from recovery import dashboard
from recovery.app import create_app

_REAL_ARTIFACTS = dashboard.DEFAULT_ARTIFACTS_DIR


def _rate(r):
    return {"rate": r, "ci_low": r - 0.04, "ci_high": r + 0.04, "n": 447}


def _snapshot(rules=None, pending=None, ledger=None) -> dict:
    block = {
        "raw_recovery": _rate(0.749),
        "control_recovery": _rate(0.132),
        "incremental_recovery": 0.617,
        "llm_share": _rate(0.559),
        "wrong_tool_rate": _rate(0.125),
        "modelled_cost_per_1000_usd": 0.9238,
    }
    return {
        "n": 500,
        "seed": 42,
        "drift_index": 250,
        "metrics": {"baseline": block, "distilled": block},
        "distillation": {"promoted": [], "demoted": []},
        "rules": rules or [],
        "ledger": ledger or [],
        "pending_approvals": pending or [],
    }


def _client(tmp_path, snapshot=None, with_chart=False, registry=None):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    if snapshot is not None:
        (artifacts / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    if with_chart:
        shutil.copy(_REAL_ARTIFACTS / "cost_curve.png", artifacts / "cost_curve.png")
    app = create_app(
        provider_registry=registry or {"fake": FakeProvider()},
        database_url=f"sqlite:///{(tmp_path / 'dash.db').as_posix()}",
        artifacts_dir=artifacts,
    )
    return TestClient(app)


def test_dashboard_renders_with_real_evaluation_artifacts(tmp_path) -> None:
    app = create_app(
        provider_registry={"fake": FakeProvider()},
        database_url=f"sqlite:///{(tmp_path / 'd.db').as_posix()}",
    )
    with TestClient(app) as client:
        html = client.get("/").text
    # Cost curve comes first, then the headline, ledger, rules, approvals.
    order = [html.index(h) for h in ("Cost curve", "Headline", "Outcome ledger", "Rules · evaluation run", "Pending approvals")]
    assert order == sorted(order)
    assert "/artifacts/cost_curve.png" in html
    assert "distilled:failure_reason=card_declined" in html
    assert "demoted" in html and "shadow" in html and "active" in html
    assert "modelled" in html


def test_dashboard_without_snapshot_degrades_gracefully(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "make demo" in response.text


def test_cost_curve_is_served_and_404s_when_absent(tmp_path) -> None:
    with _client(tmp_path / "with", snapshot=_snapshot(), with_chart=True) as client:
        ok = client.get("/artifacts/cost_curve.png")
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "image/png"
    with _client(tmp_path / "without", snapshot=_snapshot()) as client:
        assert client.get("/artifacts/cost_curve.png").status_code == 404


def test_untrusted_rule_content_is_escaped(tmp_path) -> None:
    evil = "<script>alert('x')</script>"
    rule = {
        "rule_key": f"distilled:{evil}",
        "version": 1,
        "status": "shadow",
        "priority": 50,
        "definition": {
            "precondition": {"all": [{"field": "failure_reason", "op": "eq", "value": evil}]},
            "action": {"name": evil, "params": {}},
        },
        "provenance": {"origin": evil, "prompt_version": evil},
    }
    with _client(tmp_path, snapshot=_snapshot(rules=[rule])) as client:
        html = client.get("/").text
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_pending_approvals_are_listed(tmp_path) -> None:
    pending = [{"case_id": "case_1", "action": "refund", "amount_inr": 9000.0}]
    with _client(tmp_path, snapshot=_snapshot(pending=pending)) as client:
        html = client.get("/").text
    assert "case_1" in html and "refund" in html and "9,000.00" in html


def test_dashboard_never_touches_a_provider(tmp_path) -> None:
    provider = FakeProvider()
    calls = {"n": 0}
    for name in ("get_payment", "get_order", "get_customer_history", "create_payment_link", "refund_payment"):
        original = getattr(provider, name)

        def spy(*a, _orig=original, **k):
            calls["n"] += 1
            return _orig(*a, **k)

        setattr(provider, name, spy)
    with _client(tmp_path, snapshot=_snapshot(), with_chart=True, registry={"fake": provider}) as client:
        client.get("/")
        client.get("/artifacts/cost_curve.png")
    assert calls["n"] == 0


def test_dashboard_routes_are_read_only() -> None:
    methods = {
        (route.path, method)
        for route in dashboard.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert methods == {("/", "GET"), ("/artifacts/cost_curve.png", "GET")}


def test_live_section_reflects_received_webhooks(tmp_path) -> None:
    signer = FakeProvider(webhook_secret="whsec_fake")
    from providers.types import FailureReason, PaymentMethod

    payload = signer.build_failed_event_payload(
        event_id="evt_d", payment_id="pay_dash", amount_inr=1234.0,
        failure_reason=FailureReason.EXPIRED_CARD, method=PaymentMethod.CARD,
    )
    with _client(tmp_path, snapshot=_snapshot(), registry={"fake": signer}) as client:
        client.post("/webhooks/fake", content=payload, headers={"X-Fake-Signature": signer.sign(payload)})
        html = client.get("/").text
    assert "1 normalised webhook event(s) received" in html
    assert "pay_dash" in html and "expired_card" in html
