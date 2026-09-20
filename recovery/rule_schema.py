"""The payment-domain rule schema.

Builds the :class:`agentcore.rules.Schema` that parameterises the neutral
grammar with payment vocabulary: the context fields a predicate may reference,
and the recovery actions a rule may take. Domain enums are mapped down to
allowed *string* values so the grammar never touches a domain enum class.
"""
from __future__ import annotations

from agentcore.rules import ActionSpec, FieldSpec, FieldType, ParamSpec, Schema
from providers.types import FailureReason, PaymentMethod
from recovery.actions import RecoveryAction


def payment_rule_schema() -> Schema:
    """Return the schema for payment-recovery rules."""
    reasons = frozenset(reason.value for reason in FailureReason)
    methods = frozenset(method.value for method in PaymentMethod)

    fields = {
        "failure_reason": FieldSpec(FieldType.ENUM, reasons),
        "amount_inr": FieldSpec(FieldType.NUMBER),
        "method": FieldSpec(FieldType.ENUM, methods),
        "customer_successful_payments": FieldSpec(FieldType.NUMBER),
        "customer_failed_payments": FieldSpec(FieldType.NUMBER),
        "hours_since_last_attempt": FieldSpec(FieldType.NUMBER),
        "attempt_number": FieldSpec(FieldType.NUMBER),
    }

    actions = {
        RecoveryAction.SEND_PAYMENT_LINK.value: ActionSpec(
            RecoveryAction.SEND_PAYMENT_LINK.value
        ),
        RecoveryAction.REFUND.value: ActionSpec(
            RecoveryAction.REFUND.value, {"full": ParamSpec(FieldType.BOOL)}
        ),
        RecoveryAction.WAIT_AND_RETRY.value: ActionSpec(
            RecoveryAction.WAIT_AND_RETRY.value,
            {"delay_hours": ParamSpec(FieldType.NUMBER)},
        ),
        RecoveryAction.ESCALATE_TO_HUMAN.value: ActionSpec(
            RecoveryAction.ESCALATE_TO_HUMAN.value
        ),
        RecoveryAction.NO_ACTION.value: ActionSpec(RecoveryAction.NO_ACTION.value),
    }

    return Schema(fields=fields, actions=actions)
