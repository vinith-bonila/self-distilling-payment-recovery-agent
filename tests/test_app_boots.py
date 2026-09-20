"""The FastAPI app boots and serves the health probe."""
from __future__ import annotations

from fastapi.testclient import TestClient

from recovery.app import create_app


def test_health_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:  # context manager runs the lifespan (init_db)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
