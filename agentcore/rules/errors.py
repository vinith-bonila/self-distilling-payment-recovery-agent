"""Grammar validation error."""
from __future__ import annotations


class RuleValidationError(ValueError):
    """Raised when a proposed rule violates the grammar in any way.

    Validation is all-or-nothing: a single violation rejects the whole
    proposal. This is the trust boundary for LLM output.
    """
