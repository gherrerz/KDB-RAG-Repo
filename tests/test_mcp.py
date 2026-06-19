"""Pruebas del montaje del servidor MCP sobre la API FastAPI."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from coderag.api import mcp_server
from coderag.api.mcp_server import (
    MCP_INCLUDED_OPERATIONS,
    _ensure_mcp_access,
    setup_mcp,
)


def _settings(*, enabled: bool = True, token: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        mcp_enabled=enabled,
        mcp_api_token=token,
        mcp_mount_path="/mcp",
        mcp_server_name="coderag-mcp",
    )


def test_setup_mcp_exposes_only_included_operations() -> None:
    """El servidor MCP publica exactamente las tools permitidas y ninguna admin."""
    from coderag.api.server import app

    mcp = setup_mcp(app, settings=_settings(token="secret"))
    names = {tool.name for tool in mcp.tools}

    assert names == set(MCP_INCLUDED_OPERATIONS)
    assert {"query_repo", "query_retrieval", "storage_health"} <= names
    forbidden = {
        "reset_all_data", "delete_repo", "chroma_query", "chroma_diagnostics",
        "ingest_repo", "get_job", "query_inventory",
        "list_repo_snapshots", "list_stale_repos", "list_provider_models",
    }
    assert names.isdisjoint(forbidden)


def test_setup_mcp_registers_mount_path() -> None:
    """El montaje registra la ruta /mcp en la app."""
    from coderag.api.server import app

    setup_mcp(app, settings=_settings(token="secret"))
    assert any(getattr(route, "path", "") == "/mcp" for route in app.routes)


def test_setup_mcp_forwards_identity_headers() -> None:
    """El servidor MCP reenvía los 3 headers de identidad (más authorization)."""
    from coderag.api.server import app

    mcp = setup_mcp(app, settings=_settings(token="secret"))
    assert {"x-role-id", "x-user-id", "x-country-id"} <= mcp._forward_headers
    assert "authorization" in mcp._forward_headers


def test_exposed_operations_declare_identity_headers() -> None:
    """Cada operación expuesta vía MCP declara los 3 headers opcionales."""
    from coderag.api.server import app

    schema = app.openapi()
    declared: dict[str, set[str]] = {}
    for item in schema["paths"].values():
        for op in item.values():
            if isinstance(op, dict) and op.get("operationId"):
                declared[op["operationId"]] = {
                    p["name"]
                    for p in op.get("parameters", [])
                    if p.get("in") == "header"
                }
    need = {"x-role-id", "x-user-id", "x-country-id"}
    for operation_id in MCP_INCLUDED_OPERATIONS:
        assert need <= declared.get(operation_id, set()), operation_id


def test_ensure_mcp_access_allows_matching_token(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "get_settings", lambda: _settings(token="secret"))
    # No debe lanzar.
    assert _ensure_mcp_access(token="secret") is None


def test_ensure_mcp_access_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "get_settings", lambda: _settings(token="secret"))
    with pytest.raises(HTTPException) as exc:
        _ensure_mcp_access(token="wrong")
    assert exc.value.status_code == 403


def test_ensure_mcp_access_open_without_token(monkeypatch) -> None:
    """Sin token configurado el acceso queda abierto (solo flag)."""
    monkeypatch.setattr(mcp_server, "get_settings", lambda: _settings(token=""))
    assert _ensure_mcp_access(token=None) is None


def test_ensure_mcp_access_returns_404_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server, "get_settings", lambda: _settings(enabled=False, token="secret")
    )
    with pytest.raises(HTTPException) as exc:
        _ensure_mcp_access(token="secret")
    assert exc.value.status_code == 404
