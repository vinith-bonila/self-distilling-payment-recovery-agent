"""TEMPORARY diagnostic — remove once the webhook-secret issue is resolved.

Answers one question about the RUNNING process: which webhook secret is a
provider adapter actually verifying against, compared with the process's
``<PROVIDER>_WEBHOOK_SECRET`` environment variable? It never returns a secret,
only presence, length and SHA-256 fingerprints.

Disabled by default: the route answers 404 unless the ``DIAG_TOKEN``
environment variable is set AND the request carries a matching
``X-Diag-Token`` header. Provider-neutral: the provider comes from the path.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()


def _fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@router.get("/_diag/webhook-secret/{provider_name}", include_in_schema=False)
def webhook_secret_fingerprint(provider_name: str, request: Request) -> dict:
    expected_token = os.environ.get("DIAG_TOKEN", "")
    supplied_token = request.headers.get("X-Diag-Token", "")
    if not expected_token or not hmac.compare_digest(expected_token, supplied_token):
        raise HTTPException(status_code=404, detail="Not Found")
    adapter = request.app.state.provider_registry.get(provider_name)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Not Found")

    env_value = os.environ.get(f"{provider_name.upper()}_WEBHOOK_SECRET")
    # The bytes the adapter verifies with (attribute name differs per adapter).
    in_use = getattr(adapter, "_webhook_secret", None) or getattr(adapter, "_secret", b"")
    env_bytes = (env_value or "").encode("utf-8")
    return {
        "temporary_diagnostic": True,
        "provider": provider_name,
        "env_present": env_value is not None,
        "env_length": len(env_value or ""),
        "env_sha256": _fingerprint(env_bytes),
        "env_has_surrounding_whitespace": env_value is not None and env_value != env_value.strip(),
        "env_is_printable_ascii": all(32 <= ord(c) < 127 for c in (env_value or "")),
        "in_use_length": len(in_use),
        "in_use_sha256": _fingerprint(in_use),
        "in_use_matches_env": hmac.compare_digest(in_use, env_bytes),
        "process_started_at": _PROCESS_STARTED_AT,
    }
