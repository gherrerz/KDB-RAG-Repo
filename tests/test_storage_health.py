"""Pruebas unitarias para validación de salud de storage."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from coderag.core import storage_health
from coderag.core.storage_health import StoragePreflightError


def _fake_settings() -> SimpleNamespace:
    """Construye configuración mínima para pruebas de preflight."""
    return SimpleNamespace(
        workspace_path=Path("./storage/workspace"),
        health_check_strict=True,
        health_check_timeout_seconds=2.0,
        health_check_ttl_seconds=60.0,
        health_check_openai=True,
        health_check_redis=False,
    )


def test_error_code_for_neo4j_auth_failure() -> None:
    """Clasifica errores de autenticación de Neo4j con código dedicado."""
    code = storage_health._error_code(
        "neo4j",
        "The client is unauthorized due to authentication failure.",
    )
    assert code == "neo4j_auth_failed"


def test_error_code_for_neo4j_connection_refused() -> None:
    """Clasifica errores de conexión a Neo4j con código dedicado."""
    code = storage_health._error_code(
        "neo4j",
        "Couldn't connect to 127.0.0.1:17687 (connection refused)",
    )
    assert code == "neo4j_unreachable"


def test_ensure_storage_ready_raises_when_strict_and_unhealthy(monkeypatch) -> None:
    """Lanza excepción cuando el modo estricto detecta storage no saludable."""

    def fake_run_storage_preflight(
        *,
        context: str,
        repo_id: str | None = None,
        force: bool = False,
    ) -> dict:
        return {
            "ok": False,
            "strict": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "context": context,
            "repo_id": repo_id,
            "failed_components": ["neo4j"],
            "items": [],
            "cached": False,
        }

    monkeypatch.setattr(
        storage_health,
        "run_storage_preflight",
        fake_run_storage_preflight,
    )

    with pytest.raises(StoragePreflightError):
        storage_health.ensure_storage_ready(context="query", repo_id="mall")


def test_run_storage_preflight_collects_failed_components(monkeypatch) -> None:
    """Incluye en failed_components los checks críticos que fallan."""
    storage_health._CACHE.clear()

    monkeypatch.setattr(storage_health, "get_settings", _fake_settings)

    failures = {"neo4j"}

    def fake_component_check(*, name: str, critical: bool, check_fn):
        del check_fn
        if name in failures:
            return {
                "name": name,
                "ok": False,
                "critical": critical,
                "code": "neo4j_unreachable",
                "message": "connection refused",
                "latency_ms": 1.0,
                "details": {},
            }
        return {
            "name": name,
            "ok": True,
            "critical": critical,
            "code": "ok",
            "message": "OK",
            "latency_ms": 1.0,
            "details": {},
        }

    monkeypatch.setattr(storage_health, "_run_component_check", fake_component_check)

    report = storage_health.run_storage_preflight(
        context="query",
        repo_id="mall",
        force=True,
    )

    assert report["ok"] is False
    assert report["failed_components"] == ["neo4j"]


def test_run_storage_preflight_uses_cache(monkeypatch) -> None:
    """Reutiliza cache cuando TTL está vigente para el mismo contexto."""
    storage_health._CACHE.clear()

    monkeypatch.setattr(storage_health, "get_settings", _fake_settings)

    calls = {"count": 0}

    def fake_component_check(*, name: str, critical: bool, check_fn):
        del name, critical, check_fn
        calls["count"] += 1
        return {
            "name": "component",
            "ok": True,
            "critical": True,
            "code": "ok",
            "message": "OK",
            "latency_ms": 1.0,
            "details": {},
        }

    monkeypatch.setattr(storage_health, "_run_component_check", fake_component_check)

    first = storage_health.run_storage_preflight(
        context="health",
        repo_id=None,
        force=False,
    )
    second = storage_health.run_storage_preflight(
        context="health",
        repo_id=None,
        force=False,
    )

    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["count"] == 6
