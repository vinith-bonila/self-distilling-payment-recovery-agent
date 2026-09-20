"""Payment-recovery application layer.

Webhooks, reason normalisation, the deterministic policy router, recovery
tools, the outcome ledger, the FastAPI app and the dashboard. This layer also
supplies the domain vocabulary (context fields and the action enum) that
parameterises the domain-neutral grammar machinery in ``agentcore``.
"""
