"""Contract tests for GET /info y GET /health (contrato de integración MCP Hexa)."""

from __future__ import annotations

from fastapi.testclient import TestClient

import main
from coderag.api.mcp_server import MCP_SENSITIVE_FIELDS, MCP_SERVER_TYPE
from coderag.core.settings import get_settings

app = main.app


def test_info_endpoint_returns_mcp_contract_shape() -> None:
    """GET /info expone metadata estática del contrato MCP Hexa, sin auth."""
    client = TestClient(app)

    response = client.get("/info")

    assert response.status_code == 200
    payload = response.json()
    settings = get_settings()
    assert payload["name"] == settings.mcp_server_name
    assert payload["server_type"] == MCP_SERVER_TYPE
    assert payload["sensitive_fields"] == MCP_SENSITIVE_FIELDS
    assert isinstance(payload["version"], str)
    assert isinstance(payload["description"], str)


def test_health_endpoint_includes_mcp_contract_fields() -> None:
    """GET /health preserva el shape legacy y agrega los campos del contrato."""
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    # Campos legacy (backward compatible).
    assert "ok" in payload
    assert "items" in payload
    # Campos exigidos por el contrato de integración MCP Hexa.
    assert payload["status"] in {"healthy", "degraded", "unhealthy"}
    assert payload["name"] == get_settings().mcp_server_name
    assert isinstance(payload["version"], str)
    assert isinstance(payload["uptime_s"], int)
    assert isinstance(payload["dependencies"], dict)
    for dependency in payload["dependencies"].values():
        assert dependency["status"] in {"healthy", "unhealthy"}
        assert isinstance(dependency["latency_ms"], (int, float))
