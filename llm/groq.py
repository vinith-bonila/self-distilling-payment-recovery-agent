"""Groq-backed LLM client (Llama 3.3 70B by default).

The ``groq`` SDK is imported lazily inside :meth:`complete`, so importing this
module and constructing the client never requires the dependency or the
network. Offline runs use the stub instead and never touch this path.
"""
from __future__ import annotations

import time

from agentcore.llm_client import LLMMessage, LLMResponse


class GroqLLMClient:
    """A real :class:`LLMClient` calling the Groq chat completions API."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        if not api_key:
            raise ValueError("GroqLLMClient requires a non-empty API key")
        self._api_key = api_key
        self.model = model
        self._client = None  # created lazily

    def _ensure_client(self):
        if self._client is None:
            try:
                from groq import Groq  # lazy import
            except ImportError as exc:  # pragma: no cover - depends on env
                raise RuntimeError(
                    "the 'groq' package is required to use GroqLLMClient"
                ) from exc
            self._client = Groq(api_key=self._api_key)
        return self._client

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        client = self._ensure_client()
        started = time.monotonic()
        completion = client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = (time.monotonic() - started) * 1000.0
        choice = completion.choices[0].message.content or ""
        usage = getattr(completion, "usage", None)
        return LLMResponse(
            text=choice,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            latency_ms=latency_ms,
            model=self.model,
            cached=False,
        )
