"""API tests for primary endpoints."""

from fastapi.testclient import TestClient

from coderag.api.server import app


def test_get_missing_job_returns_404() -> None:
    """Returns not found for unknown ingestion job id."""
    client = TestClient(app)
    response = client.get("/jobs/non-existent")
    assert response.status_code == 404
