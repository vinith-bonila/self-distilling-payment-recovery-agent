"""Swappable LLM clients: stub determinism, disk cache, Groq lazy construction."""
from __future__ import annotations

import json

import pytest
from _agent_support import CountingLLMClient

from agentcore.llm_client import LLMMessage
from llm.cache import CachingLLMClient
from llm.groq import GroqLLMClient
from llm.stub import StubLLMClient

_STATE = json.dumps(
    {
        "case": {"payment_id": "p", "failure_reason": "risk_blocked", "amount_inr": 100},
        "tools": [{"name": "fetch_payment"}, {"name": "escalate_to_human"}],
        "history": [],
    }
)


def test_stub_is_deterministic_and_returns_valid_decision() -> None:
    messages = [LLMMessage("system", "s"), LLMMessage("user", _STATE)]
    first = StubLLMClient().complete(messages)
    second = StubLLMClient().complete(messages)
    assert first.text == second.text
    decision = json.loads(first.text)
    assert ("action" in decision) or ("final" in decision)


def test_cache_returns_stored_response_without_calling_inner(tmp_path) -> None:
    inner = CountingLLMClient('{"final":{"resolution":null}}')
    client = CachingLLMClient(inner, tmp_path / "cache")
    messages = [LLMMessage("user", "hello")]
    first = client.complete(messages)
    second = client.complete(messages)
    assert inner.calls == 1  # second served from disk
    assert first.cached is False
    assert second.cached is True
    assert first.text == second.text


def test_cache_key_differs_by_prompt(tmp_path) -> None:
    inner = CountingLLMClient('{"x":1}')
    client = CachingLLMClient(inner, tmp_path / "cache")
    client.complete([LLMMessage("user", "a")])
    client.complete([LLMMessage("user", "b")])
    assert inner.calls == 2  # different prompts -> different keys


def test_groq_requires_api_key() -> None:
    with pytest.raises(ValueError):
        GroqLLMClient("")


def test_groq_constructs_without_network() -> None:
    client = GroqLLMClient("gsk_test_dummy", "llama-3.3-70b-versatile")
    assert client.model == "llama-3.3-70b-versatile"
    assert hasattr(client, "complete")
