"""Per-provider webhook receiver.

Contract for ``POST /webhooks/{provider_name}``:

1. Verify the signature at the boundary. A bad signature is rejected with 400
   and *nothing* is persisted or processed.
2. Persist the raw event verbatim, and commit it, BEFORE anything else.
3. Return 200. Normalisation into the internal event is best-effort and must
   never lose the raw event or turn the 200 into an error.

The receiver takes no recovery action here (the agent/router pipeline is wired
in Phase 5); it records the normalised event for downstream processing.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from providers.base import PaymentProvider
from recovery.db import session_scope
from recovery.models import InternalEvent, RawWebhookEvent

logger = logging.getLogger(__name__)

router = APIRouter()


def _persist_raw(
    provider_name: str, body: bytes, event_id: str | None, event_type: str
) -> None:
    with session_scope() as session:
        session.add(
            RawWebhookEvent(
                provider=provider_name,
                event_id=event_id or "",
                event_type=event_type,
                signature_verified=True,
                payload=body.decode("utf-8", errors="replace"),
            )
        )


def _persist_internal(event) -> None:
    with session_scope() as session:
        session.add(
            InternalEvent(
                provider=event.provider,
                event_id=event.event_id,
                event_type=event.event_type.value,
                payment_id=event.payment_id,
                amount_inr=event.amount_inr,
                failure_reason=(
                    event.failure_reason.value if event.failure_reason else None
                ),
                occurred_at=event.occurred_at.isoformat() if event.occurred_at else None,
            )
        )


@router.post("/webhooks/{provider_name}")
async def receive_webhook(provider_name: str, request: Request) -> Response:
    registry: dict[str, PaymentProvider] = request.app.state.provider_registry
    provider = registry.get(provider_name)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider")

    body = await request.body()
    signature = request.headers.get(provider.signature_header, "")

    # (1) Signature is checked before any persistence or processing.
    if not provider.verify_signature(body, signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature")

    event_id_header = getattr(provider, "event_id_header", None)
    header_event_id = (
        request.headers.get(event_id_header) if event_id_header else None
    )

    # (2) Persist the raw event and commit it before doing anything else.
    _persist_raw(provider_name, body, header_event_id, event_type="")

    # (3) Best-effort normalisation. A failure here must not lose the raw event
    #     nor turn the response into an error: the raw event is already durable.
    try:
        event = provider.parse_event(body, event_id=header_event_id)
        _persist_internal(event)
    except Exception:  # noqa: BLE001 — normalisation is best-effort by design
        logger.exception("normalisation failed for %s webhook", provider_name)

    return Response(status_code=status.HTTP_200_OK)
