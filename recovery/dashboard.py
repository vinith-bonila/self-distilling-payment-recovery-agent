"""Minimal read-only HTML dashboard at ``/``.

Deliberately simple: server-rendered HTML, no frontend framework, no JavaScript.
It only READS — the latest offline evaluation snapshot, the rule store (through
``RuleRepository``), and received webhook events. It never calls a provider,
never executes an action and exposes no mutating route.

Every dynamic value is HTML-escaped: rule definitions and provenance originate
from LLM proposals, so they are treated as untrusted display data.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from recovery.db import session_scope
from recovery.models import InternalEvent
from recovery.rule_schema import payment_rule_schema
from recovery.rules_repo import RuleRepository, RuleStatus

router = APIRouter()

DEFAULT_ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "evals"
_LEDGER_ROWS = 25
_EVENT_ROWS = 10


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _rate(block: dict[str, Any]) -> str:
    return f"{_pct(block['rate'])} <span class=muted>(CI {_pct(block['ci_low'])}–{_pct(block['ci_high'])})</span>"


def load_snapshot(artifacts_dir: Path) -> dict[str, Any] | None:
    path = artifacts_dir / "snapshot.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _precondition(definition: dict[str, Any]) -> str:
    preds = (definition.get("precondition") or {}).get("all") or []
    return " AND ".join(f"{p.get('field')} {p.get('op')} {p.get('value')}" for p in preds)


def _live_rules() -> list[dict[str, Any]]:
    repo = RuleRepository(payment_rule_schema())
    rows: list[dict[str, Any]] = []
    for status in RuleStatus:
        for loaded in repo.list_by_status(status):
            rows.append(
                {
                    "rule_key": loaded.rule_key,
                    "version": loaded.version,
                    "status": loaded.status.value,
                    "priority": loaded.priority,
                    "definition": loaded.definition,
                    "provenance": loaded.provenance,
                }
            )
    return rows


def _live_events() -> tuple[int, list[InternalEvent]]:
    with session_scope() as session:
        total = session.query(InternalEvent).count()
        latest = (
            session.query(InternalEvent)
            .order_by(InternalEvent.id.desc())
            .limit(_EVENT_ROWS)
            .all()
        )
        return total, latest


_CSS = """
:root{--bg:#fbfbfa;--fg:#1c1c1a;--muted:#6b6b66;--line:#e4e3de;--card:#fff;
--ok:#2f7d4f;--warn:#a15c00;--bad:#b3261e;--shadow:#5b5bd6}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ececea;--muted:#9a9a93;
--line:#2c2c29;--card:#1e1e1b;--ok:#6fcf97;--warn:#f2b56b;--bad:#f28b82;--shadow:#a5a5ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
.muted{color:var(--muted)}.note{font-size:13px;color:var(--muted);margin:0 0 16px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted)}td.num{text-align:right;font-variant-numeric:tabular-nums}
img{max-width:100%;height:auto;display:block;border-radius:6px;background:#fff}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid}
.active{color:var(--ok)}.shadow{color:var(--shadow)}.demoted{color:var(--bad)}.disabled{color:var(--muted)}
code{font-size:12px}
"""


def _metrics_table(snapshot: dict[str, Any]) -> str:
    b = snapshot["metrics"]["baseline"]
    d = snapshot["metrics"]["distilled"]
    rows = [
        ("Recovery (treated)", _rate(b["raw_recovery"]), _rate(d["raw_recovery"])),
        ("Control recovery (natural)", _rate(b["control_recovery"]), _rate(d["control_recovery"])),
        ("Incremental vs control", _pct(b["incremental_recovery"]), _pct(d["incremental_recovery"])),
        ("LLM share of traffic", _pct(b["llm_share"]["rate"]), _pct(d["llm_share"]["rate"])),
        (
            "Modelled cost / 1,000 failures",
            f"${b['modelled_cost_per_1000_usd']:.4f}",
            f"${d['modelled_cost_per_1000_usd']:.4f}",
        ),
        ("Wrong-tool rate", _pct(b["wrong_tool_rate"]["rate"]), _pct(d["wrong_tool_rate"]["rate"])),
    ]
    body = "".join(f"<tr><td>{_e(n)}</td><td>{bv}</td><td>{dv}</td></tr>" for n, bv, dv in rows)
    return (
        "<table><tr><th>Metric</th><th>Baseline</th><th>With distillation</th></tr>"
        f"{body}</table>"
    )


def _ledger_table(snapshot: dict[str, Any]) -> str:
    ledger = snapshot.get("ledger", [])
    treated = [r for r in ledger if r["path"] != "control"]
    recovered = [r for r in treated if r["outcome"] == "recovered"]
    total_cost = sum(r["modelled_llm_cost_usd"] for r in treated)
    total_recovered = sum(r["amount_recovered_inr"] for r in treated)
    latest = sorted(ledger, key=lambda r: r["case_index"], reverse=True)[:_LEDGER_ROWS]
    rows = "".join(
        "<tr>"
        f"<td class=num>{_e(r['case_index'])}</td><td>{_e(r['provider'])}</td>"
        f"<td class=num>₹{r['amount_inr']:,.2f}</td><td>{_e(r['path'])}</td>"
        f"<td>{_e(r['action'] or '—')}</td><td>{_e(r['outcome'])}</td>"
        f"<td class=num>₹{r['amount_recovered_inr']:,.2f}</td>"
        f"<td class=num>${r['modelled_llm_cost_usd']:.5f}</td>"
        f"<td class=num>{r['modelled_latency_ms']:.0f}</td><td class=num>{_e(r['tool_calls'])}</td>"
        f"<td><code>{_e(r['rule_key'] or '—')}</code></td>"
        "</tr>"
        for r in latest
    )
    return (
        f"<p class=note>{len(treated)} treated cases · {len(recovered)} recovered · "
        f"₹{total_recovered:,.2f} recovered · ${total_cost:.4f} modelled LLM cost. "
        f"Showing the latest {len(latest)} rows.</p>"
        "<div class=scroll><table><tr><th>Case</th><th>Provider</th><th>Amount</th><th>Path</th>"
        "<th>Action</th><th>Outcome</th><th>Recovered</th><th>LLM cost*</th><th>ms*</th>"
        f"<th>Tools</th><th>Rule</th></tr>{rows}</table></div>"
        "<p class=note>* modelled (token accounting × configured price), not real spend.</p>"
    )


def _rules_table(rules: list[dict[str, Any]], distillation: dict[str, Any]) -> str:
    if not rules:
        return "<p class=muted>No rules.</p>"
    promoted_at = {p["rule_key"]: p.get("at_case") for p in distillation.get("promoted", [])}
    demoted = {d["rule_key"]: d for d in distillation.get("demoted", [])}
    body = ""
    for r in sorted(rules, key=lambda x: (x["priority"], x["rule_key"])):
        prov = r.get("provenance") or {}
        action = (r.get("definition") or {}).get("action") or {}
        notes = [f"origin: {prov.get('origin', '?')}"]
        if prov.get("prompt_version"):
            notes.append(f"prompt: {prov['prompt_version']}")
        if prov.get("source_trajectory_ids"):
            notes.append(f"{len(prov['source_trajectory_ids'])} source trajectories")
        if r["rule_key"] in promoted_at:
            notes.append(f"promoted at case {promoted_at[r['rule_key']]}")
        if r["rule_key"] in demoted:
            dm = demoted[r["rule_key"]]
            notes.append(f"demoted at case {dm.get('at_case')}: {dm.get('reason')}")
        for entry in prov.get("demotion_history", []) or []:
            if r["rule_key"] not in demoted:
                notes.append(f"demotion: {entry.get('reason')}")
        status = r["status"]
        body += (
            "<tr>"
            f"<td><code>{_e(r['rule_key'])}</code></td>"
            f"<td><span class='pill {_e(status)}'>{_e(status)}</span></td>"
            f"<td class=num>{_e(r['version'])}</td><td class=num>{_e(r['priority'])}</td>"
            f"<td><code>{_e(_precondition(r.get('definition') or {}))}</code></td>"
            f"<td>{_e(action.get('name'))}</td>"
            f"<td>{'<br>'.join(_e(n) for n in notes)}</td>"
            "</tr>"
        )
    return (
        "<div class=scroll><table><tr><th>Rule</th><th>Status</th><th>Ver</th><th>Prio</th>"
        f"<th>Precondition</th><th>Action</th><th>Provenance</th></tr>{body}</table></div>"
    )


def _approvals(snapshot: dict[str, Any] | None) -> str:
    pending = (snapshot or {}).get("pending_approvals", [])
    if not pending:
        return (
            "<p class=muted>No actions awaiting approval. The frozen stub agent never "
            "issues refunds, so the approval gate is exercised by tests rather than by "
            "this evaluation run.</p>"
        )
    rows = "".join(
        f"<tr><td>{_e(p['case_id'])}</td><td>{_e(p['action'])}</td>"
        f"<td class=num>₹{p['amount_inr']:,.2f}</td></tr>"
        for p in pending
    )
    return f"<table><tr><th>Case</th><th>Action</th><th>Amount</th></tr>{rows}</table>"


def _live_section() -> str:
    total, latest = _live_events()
    live_rules = _live_rules()
    rows = "".join(
        f"<tr><td>{_e(e.provider)}</td><td>{_e(e.event_type)}</td><td>{_e(e.payment_id)}</td>"
        f"<td class=num>₹{e.amount_inr:,.2f}</td><td>{_e(e.failure_reason or '—')}</td></tr>"
        for e in latest
    )
    events = (
        f"<table><tr><th>Provider</th><th>Event</th><th>Payment</th><th>Amount</th>"
        f"<th>Reason</th></tr>{rows}</table>"
        if latest
        else "<p class=muted>No webhooks received by this instance yet.</p>"
    )
    return (
        f"<p class=note>{total} normalised webhook event(s) received · "
        f"{len(live_rules)} rule(s) in this instance's store.</p>{events}"
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    artifacts_dir: Path = request.app.state.artifacts_dir
    snapshot = load_snapshot(artifacts_dir)
    has_chart = (artifacts_dir / "cost_curve.png").exists()

    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>Payment Recovery</title><style>{_CSS}</style></head><body><main>",
        "<h1>Self-distilling payment recovery</h1>",
        "<p class=note>An LLM that writes the rules that replace it. Evaluation "
        "figures below are from a synthetic, offline run; cost is modelled.</p>",
        "<h2>Cost curve</h2><section>",
        "<img src='/artifacts/cost_curve.png' alt='Modelled cost per 1,000 failures, "
        "recovery rate with 95% Wilson band, and LLM share over simulated time'>"
        if has_chart
        else "<p class=muted>No chart yet — run <code>make demo</code>.</p>",
        "</section>",
    ]
    if snapshot is None:
        parts.append(
            "<h2>Evaluation</h2><section><p class=muted>No evaluation snapshot yet — "
            "run <code>make demo</code> to generate one.</p></section>"
        )
    else:
        parts += [
            f"<h2>Headline</h2><section><p class=note>{_e(snapshot['n'])} cases, seed "
            f"{_e(snapshot['seed'])}, concept drift at case {_e(snapshot['drift_index'])}.</p>",
            _metrics_table(snapshot),
            "</section>",
            "<h2>Outcome ledger</h2><section>",
            _ledger_table(snapshot),
            "</section>",
            "<h2>Rules</h2><section><p class=note>Final state of the evaluation run. "
            "Shadow rules observe only and never execute.</p>",
            _rules_table(snapshot.get("rules", []), snapshot.get("distillation", {})),
            "</section>",
        ]
    parts += [
        "<h2>Pending approvals</h2><section>",
        _approvals(snapshot),
        "</section>",
        "<h2>This instance</h2><section>",
        _live_section(),
        "</section></main></body></html>",
    ]
    return HTMLResponse("".join(parts))


@router.get("/artifacts/cost_curve.png")
def cost_curve(request: Request) -> FileResponse:
    path = request.app.state.artifacts_dir / "cost_curve.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no chart generated yet")
    return FileResponse(path, media_type="image/png")
