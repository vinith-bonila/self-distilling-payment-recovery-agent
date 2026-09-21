"""TEMPORARY: tests for the temporary webhook-secret diagnostic. Remove with it."""
from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from config import Settings
from providers.razorpay import RazorpayAdapter
from recovery.app import create_app

_SECRET = "k" * 35


def _client(tmp_path):
    adapter = RazorpayAdapter("rzp_test_x", "s", _SECRET)
    app = create_app(
        settings=Settings(_env_file=None),
        provider_registry={"razorpay": adapter},
        database_url=f"sqlite:///{(tmp_path / 'diag.db').as_posix()}",
    )
    return TestClient(app)


def test_hidden_unless_token_configured_and_supplied(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIAG_TOKEN", raising=False)
    with _client(tmp_path) as client:
        assert client.get("/_diag/webhook-secret/razorpay").status_code == 404
    monkeypatch.setenv("DIAG_TOKEN", "t0ken")
    with _client(tmp_path) as client:
        assert client.get("/_diag/webhook-secret/razorpay").status_code == 404
        bad = client.get("/_diag/webhook-secret/razorpay", headers={"X-Diag-Token": "wrong"})
        assert bad.status_code == 404


def test_reports_fingerprints_never_the_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIAG_TOKEN", "t0ken")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", _SECRET)
    with _client(tmp_path) as client:
        response = client.get("/_diag/webhook-secret/razorpay", headers={"X-Diag-Token": "t0ken"})
    assert response.status_code == 200
    body = response.json()
    fingerprint = hashlib.sha256(_SECRET.encode()).hexdigest()
    assert body["env_present"] is True and body["env_length"] == 35
    assert body["env_sha256"] == fingerprint == body["in_use_sha256"]
    assert body["in_use_matches_env"] is True
    assert _SECRET not in response.text  # the secret itself never leaves the process
