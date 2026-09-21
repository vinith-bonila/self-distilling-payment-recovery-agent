"""The FastAPI app boots and serves the health probe."""
from __future__ import annotations

from fastapi.testclient import TestClient

from recovery.app import create_app


def test_health_endpoint(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'boot.db').as_posix()}")
    with TestClient(app) as client:  # context manager runs the lifespan (init_db)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
