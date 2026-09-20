"""The raw webhook event store round-trips."""
from __future__ import annotations

import json

from recovery.db import init_db, session_scope
from recovery.models import RawWebhookEvent


def test_raw_event_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path.as_posix()}")

    with session_scope() as session:
        session.add(
            RawWebhookEvent(
                provider="fake",
                event_id="evt_1",
                event_type="payment.failed",
                signature_verified=True,
                payload=json.dumps({"amount": 4200, "reason": "insufficient_funds"}),
            )
        )

    with session_scope() as session:
        rows = session.query(RawWebhookEvent).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.provider == "fake"
        assert row.signature_verified is True
        assert json.loads(row.payload)["amount"] == 4200
