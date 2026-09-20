"""Payment-specific distillation orchestration.

Wires the domain-neutral mechanics in ``agentcore.distill`` to payment concepts:
the feature extractor, the grammar schema, the proposer prompt/LLM, and the
versioned rule store. Responsibilities:

* collect handled agent trajectories as generic Observations,
* periodically cluster them and ask the LLM to PROPOSE candidate rules in the
  closed grammar (validated wholesale before storage), starting in SHADOW,
* accumulate shadow agreement + recovery per candidate against live traffic,
* PROMOTE candidates that meet the principled thresholds — never a high-value
  refund rule,
* DEMOTE active distilled rules whose recent recovery degrades,
* record full provenance and preserve demotion history.

The cadence / minimum-cluster settings are operational knobs (how often to look,
how big a cluster must be to bother proposing); the statistically load-bearing
thresholds live in the injected PromotionPolicy / DemotionPolicy and are fixed
from principle in ``agentcore.distill.lifecycle``.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentcore.distill import (
    DemotionPolicy,
    Observation,
    PromotionPolicy,
    ShadowStats,
    Situation,
    cluster_observations,
    qualifies_for_promotion,
    should_demote,
)
from agentcore.llm_client import LLMClient, LLMMessage
from agentcore.rules import Operator, RuleValidationError, Schema, parse_rule
from config import Settings
from recovery.prompts import load_prompt
from recovery.rules_repo import LoadedRule, RuleRepository, RuleStatus

# Operational knobs (not the statistical promotion gate).
DEFAULT_CADENCE = 25
MIN_CLUSTER_SUPPORT = 15
MIN_CLUSTER_AGREEMENT = 0.80
REASON_RECOVERY_HINT = 0.60

_CONTEXT_FEATURES = ["failure_reason", "amount_band", "failure_history", "recency", "channel"]


def situation_features(context: dict[str, Any]) -> dict[str, str]:
    """Discretise an observable context into situation features.

    Bands are round business heuristics (not derived from any ground truth):
    amount in <2k / 2k-10k / >=10k, failure history none/some/many, recency
    recent/normal, plus the channel.
    """
    amount = float(context.get("amount_inr", 0.0))
    failed = int(context.get("customer_failed_payments", 0))
    hours = float(context.get("hours_since_last_attempt", 999.0))
    if amount < 2000:
        band = "lt_2000"
    elif amount < 10000:
        band = "2000_10000"
    else:
        band = "gte_10000"
    history = "many" if failed >= 3 else ("some" if failed >= 1 else "none")
    return {
        "failure_reason": str(context.get("failure_reason", "unknown")),
        "amount_band": band,
        "failure_history": history,
        "recency": "recent" if hours < 1.0 else "normal",
        "channel": str(context.get("method", "unknown")),
    }


def _band_predicates(band: str) -> list[dict[str, Any]]:
    if band == "lt_2000":
        return [{"field": "amount_inr", "op": "lt", "value": 2000}]
    if band == "2000_10000":
        return [
            {"field": "amount_inr", "op": "ge", "value": 2000},
            {"field": "amount_inr", "op": "lt", "value": 10000},
        ]
    return [{"field": "amount_inr", "op": "ge", "value": 10000}]


def _predicates_for_key(key_features: dict[str, str]) -> list[dict[str, Any]]:
    preds: list[dict[str, Any]] = [
        {"field": "failure_reason", "op": "eq", "value": key_features["failure_reason"]}
    ]
    if "amount_band" in key_features:
        preds += _band_predicates(key_features["amount_band"])
    if "failure_history" in key_features and key_features["failure_history"] == "many":
        preds.append({"field": "customer_failed_payments", "op": "ge", "value": 3})
    if "recency" in key_features and key_features["recency"] == "recent":
        preds.append({"field": "hours_since_last_attempt", "op": "lt", "value": 1})
    if "channel" in key_features:
        preds.append({"field": "method", "op": "eq", "value": key_features["channel"]})
    return preds


@dataclass
class DistillationLog:
    """Everything the harness reports about a distillation run."""

    shadow_created: list[str] = field(default_factory=list)
    promoted: list[dict[str, Any]] = field(default_factory=list)
    demoted: list[dict[str, Any]] = field(default_factory=list)
    blocked_high_value_refund: list[str] = field(default_factory=list)
    invalid_proposals: int = 0


class PaymentDistiller:
    """Streaming distiller: observe → cluster → propose → shadow → promote/demote."""

    def __init__(
        self,
        repo: RuleRepository,
        schema: Schema,
        proposer: LLMClient,
        settings: Settings,
        *,
        promotion: PromotionPolicy | None = None,
        demotion: DemotionPolicy | None = None,
        cadence: int = DEFAULT_CADENCE,
        prompt_version: str = "v1",
    ) -> None:
        self._repo = repo
        self._schema = schema
        self._proposer = proposer
        self._settings = settings
        self._promotion = promotion or PromotionPolicy()
        self._demotion = demotion or DemotionPolicy()
        self._cadence = cadence
        self._prompt = load_prompt("rule_proposer", prompt_version)

        self._pool: list[Observation] = []
        self._created_keys: set[str] = set()
        self._shadow: dict[str, ShadowStats] = {}
        self._recent: dict[str, deque[bool]] = {}
        self.log = DistillationLog()

    # --- observation ------------------------------------------------------

    def observe(
        self,
        context: dict[str, Any],
        action: str | None,
        recovered: bool,
        *,
        from_agent: bool,
        source_id: str,
    ) -> None:
        """Record a handled case and update shadow/active rule statistics."""
        situation = Situation.from_mapping(situation_features(context))
        if from_agent and action is not None:
            self._pool.append(Observation(situation, action, recovered, source_id))
        for loaded in self._distilled_rules():
            from agentcore.rules import evaluate

            if not evaluate(loaded.rule, context):
                continue
            stats = self._shadow.setdefault(loaded.rule_key, ShadowStats())
            stats.record(agreed=(loaded.rule.action.name == action), recovered=recovered)
            if loaded.status is RuleStatus.ACTIVE:
                window = self._recent.setdefault(
                    loaded.rule_key, deque(maxlen=self._demotion.min_recent)
                )
                window.append(recovered)

    def step(self, cases_seen: int) -> None:
        """Run distillation/promotion/demotion on the cadence."""
        if cases_seen % self._cadence != 0:
            return
        self.distill()
        self.promote()
        self.demote()

    # --- distillation -----------------------------------------------------

    def distill(self) -> None:
        for key_features, action, source_ids in self._candidate_situations():
            rule_key = self._rule_key(key_features)
            if rule_key in self._created_keys:
                continue
            definition = self._propose(key_features, action)
            if definition is None:
                continue
            provenance = {
                "origin": "distilled",
                "situation_key": key_features,
                "source_trajectory_ids": source_ids[:20],
                "proposal_model": getattr(self._proposer, "model", "unknown"),
                "prompt_version": self._prompt.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "originating_reason": key_features.get("failure_reason"),
                "promoted_at": None,
                "demoted_at": None,
                "demotion_history": [],
                "performance": {},
            }
            self._repo.add_rule(
                rule_key, definition, status=RuleStatus.SHADOW,
                provenance=provenance, priority=50,
            )
            self._created_keys.add(rule_key)
            self.log.shadow_created.append(rule_key)

    def _candidate_situations(self) -> list[tuple[dict[str, str], str, list[str]]]:
        candidates: list[tuple[dict[str, str], str, list[str]]] = []
        reason_clusters = cluster_observations(self._pool, ["failure_reason"])
        for cluster in reason_clusters:
            if cluster.support < MIN_CLUSTER_SUPPORT:
                continue
            if cluster.action_agreement < MIN_CLUSTER_AGREEMENT:
                candidates += self._refine(cluster.key_dict()["failure_reason"])
                continue
            if cluster.recovery_rate >= REASON_RECOVERY_HINT:
                candidates.append((cluster.key_dict(), cluster.dominant_action, cluster.source_ids))
            else:
                # Reason level looks inconsistent in outcome; try adding context.
                candidates += self._refine(cluster.key_dict()["failure_reason"])
        return candidates

    def _refine(self, reason: str) -> list[tuple[dict[str, str], str, list[str]]]:
        subset = [o for o in self._pool if o.situation.as_dict().get("failure_reason") == reason]
        out: list[tuple[dict[str, str], str, list[str]]] = []
        for cluster in cluster_observations(subset, ["failure_reason", "amount_band"]):
            if (
                cluster.support >= MIN_CLUSTER_SUPPORT
                and cluster.action_agreement >= MIN_CLUSTER_AGREEMENT
                and cluster.recovery_rate >= REASON_RECOVERY_HINT
            ):
                out.append((cluster.key_dict(), cluster.dominant_action, cluster.source_ids))
        return out

    def _propose(self, key_features: dict[str, str], action: str) -> dict[str, Any] | None:
        """Ask the proposer for a rule, then validate it wholesale."""
        suggested = {
            "precondition": {"all": _predicates_for_key(key_features)},
            "action": {"name": action, "params": self._default_params(action)},
        }
        state = {
            "task": "propose_rule",
            "situation": key_features,
            "schema": {
                "fields": list(self._schema.fields.keys()),
                "actions": list(self._schema.actions.keys()),
            },
            "suggested": suggested,
        }
        messages = [
            LLMMessage("system", self._prompt.text),
            LLMMessage("user", json.dumps(state, sort_keys=True)),
        ]
        response = self._proposer.complete(messages)
        try:
            proposal = json.loads(response.text)
            parse_rule(proposal, self._schema)  # trust boundary; rejects wholesale
        except (ValueError, TypeError, RuleValidationError):
            self.log.invalid_proposals += 1
            return None
        return proposal

    def _default_params(self, action: str) -> dict[str, Any]:
        spec = self._schema.actions.get(action)
        if spec is None:
            return {}
        params: dict[str, Any] = {}
        for name, pspec in spec.params.items():
            # Sensible defaults so the proposal is grammar-valid; the stub never
            # proposes refunds, so this only matters for non-refund params.
            params[name] = {"number": 24, "bool": False, "string": ""}.get(pspec.type.value, 0)
        return params

    # --- promotion / demotion --------------------------------------------

    def promote(self) -> None:
        for loaded in self._distilled_rules(status=RuleStatus.SHADOW):
            stats = self._shadow.get(loaded.rule_key)
            if stats is None or not qualifies_for_promotion(stats, self._promotion):
                continue
            if self._is_high_value_refund(loaded):
                if loaded.rule_key not in self.log.blocked_high_value_refund:
                    self.log.blocked_high_value_refund.append(loaded.rule_key)
                continue  # structurally never auto-promoted
            self._repo.set_status(loaded.rule_key, loaded.version, RuleStatus.ACTIVE)
            self.log.promoted.append(
                {
                    "rule_key": loaded.rule_key,
                    "support": stats.matched,
                    "agreement": round(stats.agreement_rate, 3),
                    "recovery": round(stats.recovery_rate, 3),
                }
            )

    def demote(self) -> None:
        for loaded in self._distilled_rules(status=RuleStatus.ACTIVE):
            window = self._recent.get(loaded.rule_key)
            if window is None:
                continue
            recovered = sum(window)
            total = len(window)
            if should_demote(recovered, total, self._demotion):
                reason = (
                    f"recent recovery {recovered}/{total} upper bound below "
                    f"{self._demotion.recovery_ub_floor}"
                )
                self._repo.set_status(
                    loaded.rule_key, loaded.version, RuleStatus.DEMOTED, reason=reason
                )
                self.log.demoted.append(
                    {"rule_key": loaded.rule_key, "recent_recovery": f"{recovered}/{total}", "reason": reason}
                )
                window.clear()

    # --- helpers ----------------------------------------------------------

    def _is_high_value_refund(self, loaded: LoadedRule) -> bool:
        """A refund rule is 'high value' unless its precondition bounds the amount
        strictly below the approval threshold. High-value refund rules are never
        auto-promoted."""
        if loaded.rule.action.name != "refund":
            return False
        threshold = self._settings.approval_threshold_inr
        for predicate in loaded.rule.precondition:
            bounded = (
                predicate.field == "amount_inr"
                and predicate.op in {Operator.LT, Operator.LE}
                and isinstance(predicate.value, (int, float))
                and predicate.value <= threshold
            )
            if bounded:
                return False
        return True

    def _distilled_rules(self, status: RuleStatus | None = None) -> list[LoadedRule]:
        statuses = [status] if status else [RuleStatus.SHADOW, RuleStatus.ACTIVE]
        rules: list[LoadedRule] = []
        for st in statuses:
            rules += [
                loaded
                for loaded in self._repo.list_by_status(st)
                if loaded.provenance.get("origin") == "distilled"
            ]
        return rules

    @staticmethod
    def _rule_key(key_features: dict[str, str]) -> str:
        parts = ";".join(f"{k}={v}" for k, v in sorted(key_features.items()))
        return f"distilled:{parts}"

    def average_shadow_agreement(self) -> float:
        rates = [s.agreement_rate for s in self._shadow.values() if s.matched]
        return sum(rates) / len(rates) if rates else 0.0
