"""Choose the live agent's LLM client from settings.

The frozen offline stub is the default: it needs no key and no network, and its
behaviour is exactly the stub used by the evaluation. Groq is opt-in
(``LLM_BACKEND=groq``) and is wrapped in the existing disk cache when
``LLM_CACHE_DIR`` is set. Settings validation already refuses ``groq`` without
a key, so this never silently falls back.
"""
from __future__ import annotations

from agentcore.llm_client import LLMClient
from config import Settings
from llm.cache import CachingLLMClient
from llm.groq import GroqLLMClient
from llm.stub import StubLLMClient


def build_llm(settings: Settings) -> LLMClient:
    """Return the configured LLM client."""
    if settings.llm_backend == "groq":
        client: LLMClient = GroqLLMClient(settings.groq_api_key, settings.groq_model)
        if settings.llm_cache_dir:
            client = CachingLLMClient(client, settings.llm_cache_dir)
        return client
    return StubLLMClient()
