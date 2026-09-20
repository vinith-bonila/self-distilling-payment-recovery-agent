"""Webhook receiver behaviour.

Proves: signature checked at the boundary (bad signature rejected without any
persistence or processing); the raw event is persisted before the 200 and
independently of normalisation (a normalisation failure still leaves the raw
event stored and still returns 200).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from providers.fake import FakeProvider
from providers.types import FailureReason, PaymentMethod
from recovery.app import create_app
from recovery.db import session_scope
from recovery.models import InternalEvent, RawWebhookEvent

_SECRET = "whsec_fake"


def _app(tmp_path):
    registry = {"fake": FakeProvider(webhook_secret=_SECRET)}
    return create_app(
        provider_registry=registry,
        database_url=f"sqlite:///{(tmp_path / 'wh.db').as_posix()}",
    )


def _counts():
    with session_scope() as session:
        return (
            session.query(RawWebhookEvent).count(),
            session.query(InternalEvent).count(),
        )


def test_valid_webhook_persists_raw_and_internal_and_returns_200(tmp_path) -> None:
    app = _app(tmp_path)
    signer = FakeProvider(webhook_secret=_SECRET)
    payload = signer.build_failed_event_payload(
        event_id="evt_1",
        payment_id="pay_1",
        amount_inr=2500.0,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
        method=PaymentMethod.CARD,
    )
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/fake",
            content=payload,
            headers={"X-Fake-Signature": signer.sign(payload), "X-Fake-Event-Id": "evt_1"},
        )
        assert response.status_code == 200

    raw_count, internal_count = _counts()
    assert raw_count == 1
    assert internal_count == 1
    with session_scope() as session:
        event = session.query(InternalEvent).one()
        assert event.payment_id == "pay_1"
        assert event.failure_reason == "insufficient_funds"  # normalised enum value
        assert event.amount_inr == 2500.0


def test_bad_signature_rejected_without_processing(tmp_path) -> None:
    app = _app(tmp_path)
    payload = b'{"event_id":"e","event":"payment.failed","payment_id":"p",'
    payload += b'"amount_inr":1,"failure_reason":"card_declined","method":"card",'
    payload += b'"occurred_at":"2020-01-01T00:00:00+00:00"}'
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/fake", content=payload, headers={"X-Fake-Signature": "deadbeef"}
        )
        assert response.status_code == 400

    assert _counts() == (0, 0)  # nothing persisted, nothing processed


def test_raw_persisted_even_when_normalisation_fails(tmp_path) -> None:
    app = _app(tmp_path)
    signer = FakeProvider(webhook_secret=_SECRET)
    body = b"this is not valid json"  # signature is valid, but parse_event will fail
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/fake", content=body, headers={"X-Fake-Signature": signer.sign(body)}
        )
        assert response.status_code == 200  # still 200

    raw_count, internal_count = _counts()
    assert raw_count == 1  # raw persisted before/independently of normalisation
    assert internal_count == 0  # normalisation produced nothing, but did not fail the request


def test_unknown_provider_returns_404(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/webhooks/nope", content=b"{}", headers={})
        assert response.status_code == 404
