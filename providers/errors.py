"""Normalised provider exceptions.

Adapters translate transport- and SDK-specific failures (httpx errors, HTTP
4xx/5xx bodies, provider error codes) into these. Layers above ``providers``
catch only these types and never see an ``httpx`` or provider-native exception.
"""
from __future__ import annotations


class ProviderError(Exception):
    """Base class for every error surfaced by a provider adapter."""


class ResourceNotFound(ProviderError):
    """A referenced payment, order or customer does not exist at the provider."""


class ProviderAPIError(ProviderError):
    """The provider returned an unexpected error response."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"provider API error {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body
