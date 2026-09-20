"""Disk-caching wrapper for any LLM client, keyed on the prompt hash.

Wraps an inner :class:`LLMClient`. On a cache hit the stored response is
returned (with ``cached=True``) and the inner client is never called, so reruns
are cheap and deterministic and can be committed for offline demos. The key is a
SHA-256 over the messages and sampling parameters.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentcore.llm_client import LLMClient, LLMMessage, LLMResponse


def _key(messages: list[LLMMessage], temperature: float, max_tokens: int) -> str:
    payload = {
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class CachingLLMClient:
    """Cache another client's completions on disk, keyed on prompt hash."""

    def __init__(self, inner: LLMClient, cache_dir: str | Path) -> None:
        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        key = _key(messages, temperature, max_tokens)
        path = self._path(key)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return LLMResponse(
                text=data["text"],
                prompt_tokens=data.get("prompt_tokens", 0),
                completion_tokens=data.get("completion_tokens", 0),
                latency_ms=data.get("latency_ms", 0.0),
                model=data.get("model", "unknown"),
                cached=True,
            )
        response = self._inner.complete(
            messages, temperature=temperature, max_tokens=max_tokens
        )
        path.write_text(
            json.dumps(
                {
                    "text": response.text,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "latency_ms": response.latency_ms,
                    "model": response.model,
                }
            ),
            encoding="utf-8",
        )
        return response
