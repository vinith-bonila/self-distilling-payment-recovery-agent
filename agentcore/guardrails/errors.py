"""Exceptions raised by the guardrail layer."""
from __future__ import annotations


class GuardError(Exception):
    """Base class for all guardrail errors."""


class UnknownActionError(GuardError):
    """Raised when an action has no registered effect handler.

    There is no unguarded fallback: an action the executor cannot resolve to a
    registered handler is refused rather than silently ignored or run outside
    the guarded path.
    """


class ApprovalError(GuardError):
    """Raised on an invalid approval operation (unknown id, wrong run, ...)."""
