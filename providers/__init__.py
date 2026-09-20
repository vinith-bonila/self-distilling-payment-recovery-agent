"""Payment provider adapters.

Exposes the :class:`~providers.base.PaymentProvider` interface and the
normalised :class:`~providers.base.FailureReason` enum. Every adapter maps its
provider-specific failure strings onto that enum so nothing above this layer
ever sees a raw provider string.
"""
