"""Deterministic shadow control group.

A fixed share of failed payments are held out and receive NO recovery action, so
their natural recovery can be measured. Membership is a deterministic function of
the case id, so it is stable across runs and independent of anything the recovery
system does. All headline recovery numbers are reported *incremental* to this
control, never raw alone.
"""
from __future__ import annotations

import hashlib

from evals.generator import SyntheticCase

DEFAULT_CONTROL_SHARE = 0.10


def is_control(case: SyntheticCase, share: float = DEFAULT_CONTROL_SHARE) -> bool:
    """True iff ``case`` is in the held-out control group."""
    bucket = int(hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:8], 16) % 1000
    return bucket < int(share * 1000)
