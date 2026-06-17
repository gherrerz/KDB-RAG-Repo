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
    assert {"ingest_repo", "query_repo", "storage_health"} <= names
    forbidden = {"reset_all_data", "delete_repo", "chroma_query", "chroma_diagnostics"}
    assert names.isdisjoint(forbidden)


def test_setup_mcp_registers_mount_path() -> None:
    """El montaje registra la ruta /mcp en la app."""
    from coderag.api.server import app

    setup_mcp(app, settings=_settings(token="secret"))
    assert any(getattr(route, "path", "") == "/mcp" for route in app.routes)


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
