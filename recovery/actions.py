"""The recovery action space.

These are the actions a rule (or the agent) may resolve a failed payment to.
They are the payment-domain action vocabulary that parameterises the neutral
grammar in ``agentcore.rules``.
"""
from __future__ import annotations

import enum


class RecoveryAction(enum.Enum):
    SEND_PAYMENT_LINK = "send_payment_link"
    REFUND = "refund"
    WAIT_AND_RETRY = "wait_and_retry"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    NO_ACTION = "no_action"
