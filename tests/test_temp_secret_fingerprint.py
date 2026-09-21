"""TEMPORARY: tests for the startup webhook-secret fingerprint. Remove with it."""
from __future__ import annotations

import hashlib
import re

from config import Settings
from recovery.providers_registry import build_registry

_LINE = re.compile(
    r"^Razorpay webhook secret fingerprint: present=(true|false) length=(\d+) sha256=([0-9a-f]{64})$"
)


def test_prints_presence_length_and_fingerprint_never_the_secret(monkeypatch, capsys) -> None:
    secret = "q" * 35
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    build_registry(Settings(_env_file=None))
    out = capsys.readouterr().out
    assert secret not in out
    (line,) = [l for l in out.splitlines() if l.startswith("Razorpay webhook secret fingerprint")]
    present, length, digest = _LINE.match(line).groups()
    assert (present, int(length)) == ("true", 35)
    assert digest == hashlib.sha256(secret.encode()).hexdigest()


def test_reports_absence(monkeypatch, capsys) -> None:
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    build_registry(Settings(_env_file=None))
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("Razorpay webhook")][0]
    assert _LINE.match(line).groups()[:2] == ("false", "0")
