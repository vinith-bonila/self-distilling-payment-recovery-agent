# Provider adapters — what is actually implemented

All adapters implement the same `PaymentProvider` contract and are exercised by
the same conformance suite (`providers/conformance.py`). Everything above
`providers/` sees only the normalised types in `providers/types.py`.

## Implementation approach (all adapters)

Both real adapters talk to the provider's **REST API over `httpx`** — we do not
depend on the `razorpay` or `stripe` Python SDKs. `httpx` is already in the
project's frozen dependency set, and calling REST directly keeps the dependency
surface small and makes every call trivially mockable with
`httpx.MockTransport`. **This is not an SDK integration.**

All network calls are mocked in tests. No adapter has been exercised against a
live provider account; what is verified is the contract behaviour against
recorded/mocked HTTP shapes.

## Razorpay

| Concept | Razorpay mapping |
| --- | --- |
| Payment | `GET /payments/{id}` |
| Order | `GET /orders/{id}` |
| Customer history | `GET /customers/{id}` + `GET /payments?count=100`, filtered client-side |
| Payment link | `POST /payment_links` |
| Refund | `POST /payments/{id}/refund` |
| Webhook signature | HMAC-SHA256 of the raw body, `X-Razorpay-Signature` |

**Limitation:** Razorpay has no per-customer payments query, so customer history
pages the payments list and filters client-side. Production would keep a local
index or paginate properly.

## Stripe

| Concept | Stripe mapping |
| --- | --- |
| Payment | PaymentIntent — `GET /payment_intents/{id}` |
| Order | Checkout Session — `GET /checkout/sessions/{id}` |
| Customer history | `GET /customers/{id}` + `GET /payment_intents?customer=…` (server-side filter) |
| Payment link | Checkout Session — `POST /checkout/sessions` |
| Refund | `POST /refunds` with a native `Idempotency-Key` header |
| Webhook signature | `Stripe-Signature: t=…,v1=…`, HMAC-SHA256 over `"{t}.{body}"` |

**Honest limitations:**

- **Stripe has no Razorpay-style Order.** We map "order" to a Checkout Session,
  the closest object representing an intended purchase. Stripe's Orders API is
  not part of the modern payments surface.
- **Payment links are Checkout Sessions, not the Payment Links API.** Payment
  Links require pre-created `Price` objects; Checkout Sessions accept an inline
  `price_data` amount, which is what recovery needs for an arbitrary amount.
- **`order_id` comes from `metadata.order_id`** — a PaymentIntent carries no
  first-class order reference.
- **Customer history counts any non-`succeeded` intent as failed.** That is a
  coarse reading of Stripe's intent lifecycle (`requires_payment_method`,
  `canceled`, `processing`, …).
- Failure reasons are normalised from `last_payment_error.decline_code`, falling
  back to `.code`; unrecognised codes become `FailureReason.UNKNOWN` rather than
  being guessed.

## Safety

Every adapter refuses non-sandbox credentials at construction (`rzp_test_`,
`sk_test_`), in addition to the startup assertion in `config.py`.

## Adding a provider

1. Write one file in `providers/` implementing `PaymentProvider`, mapping the
   provider's strings/amounts onto `providers/types.py`.
2. Add a harness to `tests/test_provider_conformance.py` and register the
   adapter in `recovery/providers_registry.py` (a flat mapping — no branching).

Nothing above `providers/` changes. `tests/test_provider_leakage.py` enforces
this: `agentcore` never mentions a provider, `recovery` mentions one only in the
registry, and the same agent/guardrails/trajectory path runs against Stripe
unchanged.
