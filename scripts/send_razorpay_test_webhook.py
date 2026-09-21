"""Send a signed Razorpay-style ``payment.failed`` webhook to a local app.

For the manual TEST-mode procedure in docs/RAZORPAY_TEST_MODE.md. Stdlib only.

It signs the body exactly as Razorpay does — HMAC-SHA256 of the raw body with
your webhook secret, hex-encoded, in ``X-Razorpay-Signature`` — so the app's
verification at the boundary is exercised for real. The payment id must be a
real payment in YOUR Razorpay TEST account: the app enriches every event by
fetching that payment from Razorpay's API with your rzp_test_ keys.

    python scripts/send_razorpay_test_webhook.py --payment-id pay_XXXXXXXXXXXX \
        --amount-inr 499 --error-reason insufficient_funds

Refuses to run unless RAZORPAY_KEY_ID (if set) is a rzp_test_ key.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def build_payload(
    payment_id: str,
    amount_inr: float,
    error_reason: str,
    *,
    method: str = "card",
    customer_id: str | None = None,
) -> bytes:
    """A Razorpay-shaped ``payment.failed`` webhook body (amounts in paise)."""
    entity = {
        "id": payment_id,
        "entity": "payment",
        "amount": int(round(amount_inr * 100)),
        "currency": "INR",
        "status": "failed",
        "method": method,
        "error_reason": error_reason,
        "created_at": int(time.time()),
    }
    if customer_id:
        entity["customer_id"] = customer_id
    return json.dumps(
        {"entity": "event", "event": "payment.failed", "payload": {"payment": {"entity": entity}}}
    ).encode("utf-8")


def sign(secret: str, body: bytes) -> str:
    """Razorpay's webhook signature: hex HMAC-SHA256 of the raw body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--payment-id", required=True, help="a real payment id in your TEST account")
    parser.add_argument("--amount-inr", type=float, required=True)
    parser.add_argument("--error-reason", default="insufficient_funds",
                        help="Razorpay error_reason, e.g. insufficient_funds, payment_declined")
    parser.add_argument("--method", default="card")
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--event-id", default=None, help="reuse one to test duplicate delivery")
    parser.add_argument("--url", default="http://127.0.0.1:8000/webhooks/razorpay")
    parser.add_argument("--secret", default=os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""))
    parser.add_argument("--bad-signature", action="store_true", help="send a wrong signature")
    args = parser.parse_args(argv)

    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    if key_id and not key_id.startswith("rzp_test_"):
        print("refusing: RAZORPAY_KEY_ID is not a rzp_test_ key", file=sys.stderr)
        return 2
    if not args.secret:
        print("set RAZORPAY_WEBHOOK_SECRET (or pass --secret)", file=sys.stderr)
        return 2

    body = build_payload(
        args.payment_id, args.amount_inr, args.error_reason,
        method=args.method, customer_id=args.customer_id,
    )
    signature = sign(args.secret, body)
    if args.bad_signature:
        signature = "0" * 64
    event_id = args.event_id or f"evt_manual_{uuid.uuid4().hex[:12]}"

    request = urllib.request.Request(
        args.url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Razorpay-Signature": signature,
                 "X-Razorpay-Event-Id": event_id},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(f"{response.status} event_id={event_id}")
    except urllib.error.HTTPError as exc:
        print(f"{exc.code} event_id={event_id} ({exc.read().decode('utf-8', 'replace')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
