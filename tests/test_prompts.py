"""Prompts are versioned files and the agent loads them (no inline strings)."""
from __future__ import annotations

from _agent_support import case, provider_with_payment, settings

from llm.stub import StubLLMClient
from recovery.agent import build_agent
from recovery.prompts import load_prompt


def test_agent_prompt_loads_with_front_matter() -> None:
    prompt = load_prompt("agent_decider", "v1")
    assert prompt.id == "agent_decider.v1"
    assert prompt.purpose  # front-matter parsed
    assert prompt.output
    assert "JSON" in prompt.text


def test_agent_uses_the_versioned_prompt_file() -> None:
    loop = build_agent(case(), provider_with_payment(), StubLLMClient(), settings())
    assert loop._system_prompt_id == "agent_decider.v1"
    assert load_prompt("agent_decider", "v1").text == loop._system_prompt
