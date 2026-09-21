# Razorpay TEST mode — manual end-to-end procedure

This walks a real Razorpay **test-mode** webhook through the live application:
signature check → raw persistence → normalisation → policy router → rule or
agent → guarded executor → Razorpay TEST API → trajectory → ledger → dashboard.

> **Status.** The signed-webhook step (4B) has been run against the Render
> deployment with a synthetic payment id: a correctly signed body returned 200
> and was recorded — as `failed`, because the payment is not real — and a
> wrongly signed body returned 400. The full path with a real failed payment in
> a Razorpay test account has not yet been run. That path is covered by an
> automated test against a *mocked* Razorpay API, using the exact payload and
> signature this procedure sends
> (`tests/test_live_pipeline.py::test_razorpay_webhook_from_the_manual_procedure_runs_the_live_pipeline`).
> What remains unverified is listed at the end. CI never needs a Razorpay
> account.

## 0. What you need

- A Razorpay account switched to **Test Mode**, with test API keys
  (`rzp_test_…` key id and its secret). Live keys are refused: the app will not
  boot with a key id that does not start with `rzp_test_`.
- A webhook secret of your choosing.
- Python with the project installed (`make install`), or Docker.

## 1. Configure test keys

```bash
cp .env.example .env
```

Edit `.env`:

```text
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=your-webhook-secret
LLM_BACKEND=stub
```

`LLM_BACKEND=stub` keeps the agent on the frozen offline stub (no LLM key, no
LLM network calls). Provider calls to Razorpay's TEST API do go over the
network.

## 2. Start the application

```bash
python -m uvicorn recovery.app:app --port 8000
```

or, with Docker:

```bash
docker run --rm --env-file .env -p 8000:8000 payment-recovery
```

Check it is up, then open the dashboard at <http://localhost:8000/>:

```bash
curl -fsS http://localhost:8000/health
```

On first start the rule store is seeded with the two seed rules
(`risk_blocked → escalate`, `invalid_details → send a payment link`).

## 3. Create a failed test payment

The pipeline enriches every event by fetching the payment from Razorpay
(`GET /v1/payments/{id}`), so the webhook must name a **real payment in your
test account**. Use Razorpay's test-mode checkout to make a payment attempt that
fails; Razorpay's test-mode documentation lists the current ways to simulate a
failure. Note its payment id (`pay_…`) and, if present, its customer id.

## 4. Send a webhook

Choose one.

**A — let Razorpay deliver it.** Expose the local app through a tunnel of your
choice, then in the Razorpay dashboard (Test Mode) → Webhooks, add
`https://<your-tunnel>/webhooks/razorpay` with your webhook secret and subscribe
to `payment.failed`. Re-run the failing payment.

**B — sign and send it locally (no tunnel).** The helper signs the body exactly
as Razorpay does (hex HMAC-SHA256 of the raw body):

```bash
python scripts/send_razorpay_test_webhook.py --payment-id pay_XXXXXXXXXXXX --amount-inr 499 --error-reason insufficient_funds
```

It prints the HTTP status and the event id it used. Expected: `200`.

## 5. Verify the raw event was persisted

```bash
python -c "import sqlite3; c=sqlite3.connect('payment_recovery.db'); print(c.execute('select id, provider, event_id, signature_verified from raw_webhook_events').fetchall())"
```

Expected: one row, provider `razorpay`, `signature_verified = 1`.

## 6. Verify normalisation

```bash
python -c "import sqlite3; c=sqlite3.connect('payment_recovery.db'); print(c.execute('select provider, event_type, payment_id, amount_inr, failure_reason from internal_events').fetchall())"
```

Expected: `event_type = payment_failed`, the amount in **rupees** (paise
converted), and `failure_reason` as the shared enum value (e.g.
`insufficient_funds`). A Razorpay `error_reason` the adapter does not recognise
becomes `unknown` rather than a guess.

## 7. Observe the router

```bash
python -c "import sqlite3; c=sqlite3.connect('payment_recovery.db'); print(c.execute('select payment_id, route, matched_rule_key, resolved_action from internal_events').fetchall())"
```

- `--error-reason insufficient_funds` → no ACTIVE rule matches →
  `route = escalate_to_agent`: the agent ran.
- `--error-reason invalid_card` (normalised to `invalid_details`) →
  `route = deterministic`, `matched_rule_key = seed-invalid-details-resend-link`:
  the seed rule handled it and **the LLM was never called**.

## 8. Observe the guarded action and the ledger

```bash
python -c "import sqlite3; c=sqlite3.connect('payment_recovery.db'); print(c.execute('select path, action, execution_status, outcome, rule_key, trajectory_id, error from ledger_entries').fetchall())"
```

```bash
python -c "import sqlite3; c=sqlite3.connect('payment_recovery.db'); print(c.execute('select seq, action_name, idempotency_key, cost, decision from guardrail_audit').fetchall())"
```

Expected for a payment-link outcome: a ledger row with
`action = send_payment_link`, `execution_status = executed`,
`outcome = resolved`, and a `guardrail_audit` row `executed` for key
`link:<payment id>`. In the Razorpay dashboard (Test Mode) → Payment Links, the
link appears. An `escalate_to_human` outcome has no Razorpay side effect.

If Razorpay rejects a call, the ledger row has `outcome = failed` and the error
text; the raw and normalised events are still stored.

## 9. Duplicate delivery and a bad signature

Re-send with the event id printed in step 4B:

```bash
python scripts/send_razorpay_test_webhook.py --payment-id pay_XXXXXXXXXXXX --amount-inr 499 --event-id evt_manual_XXXXXXXXXXXX
```

Expected: `200`, a second raw event, and a ledger row with
`path = duplicate_delivery` — no second payment link.

```bash
python scripts/send_razorpay_test_webhook.py --payment-id pay_XXXXXXXXXXXX --amount-inr 499 --bad-signature
```

Expected: `400`, and nothing new in any table.

## 10. Inspect the dashboard and trajectory

<http://localhost:8000/> → **Live application** shows the live outcome ledger,
received webhooks and the rule store; **Pending approvals** lists any live action
held for approval. For an agent-path row, its trajectory is in `trajectories`:

```bash
python -c "import sqlite3,json; c=sqlite3.connect('payment_recovery.db'); r=c.execute('select trajectory_json from trajectories order by id desc limit 1').fetchone(); print(json.dumps(json.loads(r[0])['steps'], indent=1))"
```

To start over, stop the app and delete `payment_recovery.db`.

## What remains manual or unverified

- **No successful call from this codebase to Razorpay's API has been
  observed.** Every Razorpay response shape the adapter relies on is an
  assumption encoded in test mocks.
- **Payment-link creation** sends `amount`, `currency`, `description` and
  `reference_id` (the payment id). Whether Razorpay accepts exactly this body is
  unverified; a second link for the same payment is prevented by the executor's
  idempotency key before Razorpay is asked.
- **Webhook payload fidelity**: the real `payment.failed` body, the `error_reason`
  values Razorpay actually sends, and the presence of `X-Razorpay-Event-Id` are
  unverified. Unmapped reasons fall back to `unknown`. If the event-id header is
  absent, the adapter derives an id from the event type and payment id, so
  redeliveries are still de-duplicated — but a genuinely distinct second failure
  of the same payment would then be treated as a redelivery.
- **Customer history** pages the first 100 payments and filters them
  client-side, because Razorpay's payments list is not customer-filtered.
- **Refunds and the approval gate** are not reachable here with default
  settings: the frozen stub agent never refunds. Both are covered by automated
  tests. Approving a pending action is deliberately **not** exposed over HTTP
  (there is no authentication), so a pending approval stays pending.
- **Replay protection** is limited to event-id de-duplication; Razorpay
  signatures carry no timestamp, so an old signed body can be replayed with a new
  event id.
