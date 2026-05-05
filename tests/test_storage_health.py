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
        resolve_chroma_hnsw_space=lambda: "cosine",
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


def test_evaluate_embedding_compatibility_detects_dimension_mismatch() -> None:
    """Marca incompatibilidad cuando consulta e ingesta usan dimensiones distintas."""
    result = storage_health.evaluate_embedding_compatibility(
        runtime_payload={
            "last_embedding_provider": "openai",
            "last_embedding_model": "text-embedding-3-small",
        },
        requested_embedding_provider="vertex_ai",
        requested_embedding_model="text-embedding-005",
    )

    assert result["embedding_compatible"] is False
    assert result["compatibility_reason"] == "embedding_dimension_mismatch"


def test_get_repo_query_status_blocks_ready_on_embedding_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cuando hay mismatch dimensional, query_ready debe ser falso aun con Chroma/BM25 disponibles."""
    monkeypatch.setattr(
        storage_health,
        "_count_chroma_documents_for_repo",
        lambda **kwargs: 5,
    )
    monkeypatch.setattr(storage_health.GLOBAL_BM25, "ensure_repo_loaded", lambda repo_id: True)
    monkeypatch.setattr(storage_health, "_check_repo_graph_available", lambda **kwargs: True)
    monkeypatch.setattr(
        storage_health.ChromaIndex,
        "collection_hnsw_spaces",
        lambda self: {
            "code_symbols": "cosine",
            "code_files": "cosine",
            "code_modules": "cosine",
        },
    )

    status = storage_health.get_repo_query_status(
        repo_id="repo-a",
        listed_in_catalog=True,
        runtime_payload={
            "last_embedding_provider": "openai",
            "last_embedding_model": "text-embedding-3-small",
        },
        requested_embedding_provider="vertex_ai",
        requested_embedding_model="text-embedding-005",
    )

    assert status["embedding_compatible"] is False
    assert status["compatibility_reason"] == "embedding_dimension_mismatch"
    assert status["query_ready"] is False


def test_get_repo_query_status_blocks_ready_on_hnsw_space_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bloquea readiness cuando CHROMA_HNSW_SPACE difiere de colecciones existentes."""
    monkeypatch.setattr(
        storage_health,
        "_count_chroma_documents_for_repo",
        lambda **kwargs: 5,
    )
    monkeypatch.setattr(storage_health.GLOBAL_BM25, "ensure_repo_loaded", lambda repo_id: True)
    monkeypatch.setattr(storage_health, "_check_repo_graph_available", lambda **kwargs: True)
    monkeypatch.setattr(
        storage_health.ChromaIndex,
        "collection_hnsw_spaces",
        lambda self: {
            "code_symbols": "l2",
            "code_files": "l2",
            "code_modules": "l2",
        },
    )

    status = storage_health.get_repo_query_status(
        repo_id="repo-a",
        listed_in_catalog=True,
        runtime_payload={
            "last_embedding_provider": "openai",
            "last_embedding_model": "text-embedding-3-small",
        },
        requested_embedding_provider="openai",
        requested_embedding_model="text-embedding-3-small",
    )

    assert status["chroma_hnsw_space_compatible"] is False
    assert status["chroma_hnsw_space_configured"] == "cosine"
    assert status["query_ready"] is False
    assert status["chroma_hnsw_space_mismatched_collections"] == [
        "code_files",
        "code_modules",
        "code_symbols",
    ]


def test_get_repo_query_status_reports_workspace_availability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expone si el repo conserva workspace local sin mezclarlo con query_ready."""

    workspace_path = tmp_path / "workspace"
    (workspace_path / "repo-a").mkdir(parents=True, exist_ok=True)

    settings = _fake_settings()
    settings.workspace_path = workspace_path
    settings.resolve_embedding_provider = lambda provider: provider or "vertex"
    settings.resolve_embedding_model = (
        lambda provider, model: model or "text-embedding-005"
    )

    monkeypatch.setattr(storage_health, "get_settings", lambda: settings)
    monkeypatch.setattr(
        storage_health,
        "_count_chroma_documents_for_repo",
        lambda **kwargs: 5,
    )
    monkeypatch.setattr(
        storage_health.GLOBAL_BM25,
        "ensure_repo_loaded",
        lambda repo_id: True,
    )
    monkeypatch.setattr(storage_health, "_check_repo_graph_available", lambda **kwargs: True)
    monkeypatch.setattr(
        storage_health.ChromaIndex,
        "collection_hnsw_spaces",
        lambda self: {
            "code_symbols": "cosine",
            "code_files": "cosine",
            "code_modules": "cosine",
        },
    )

    available_status = storage_health.get_repo_query_status(
        repo_id="repo-a",
        listed_in_catalog=True,
    )
    missing_status = storage_health.get_repo_query_status(
        repo_id="repo-missing",
        listed_in_catalog=True,
    )

    assert available_status["workspace_available"] is True
    assert available_status["query_ready"] is True
    assert missing_status["workspace_available"] is False
    assert missing_status["query_ready"] is True


def test_check_chroma_raises_on_hnsw_space_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falla preflight cuando el espacio HNSW de colecciones no coincide."""

    class _FakeIndex:
        client = SimpleNamespace(list_collections=lambda: ["code_symbols"])
        collections = {"code_symbols": object()}

        def collection_hnsw_spaces(self) -> dict[str, str]:
            return {"code_symbols": "l2"}

    monkeypatch.setattr(storage_health, "ChromaIndex", _FakeIndex)
    monkeypatch.setattr(storage_health, "get_settings", _fake_settings)

    with pytest.raises(RuntimeError, match="Espacio HNSW inconsistente"):
        storage_health._check_chroma()


def test_check_bm25_raises_when_repo_not_loaded_in_query_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lanza error cuando BM25 no está cargado para el repo en contexto query."""
    monkeypatch.setattr(
        storage_health.GLOBAL_BM25,
        "ensure_repo_loaded",
        lambda repo_id: False,
    )

    with pytest.raises(RuntimeError, match="No hay índice BM25 cargado"):
        storage_health._check_bm25(context="query", repo_id="mall")


def test_run_storage_preflight_marks_bm25_as_non_critical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporta BM25 como fallo no crítico cuando falta índice en query."""
    storage_health._CACHE.clear()
    monkeypatch.setattr(storage_health, "get_settings", _fake_settings)
    monkeypatch.setattr(storage_health, "_check_workspace", lambda path: {"path": str(path)})
    monkeypatch.setattr(
        storage_health,
        "_check_metadata_sqlite",
        lambda db_path: {"db_path": str(db_path)},
    )
    monkeypatch.setattr(storage_health, "_check_chroma", lambda: {"collection_count": 0})
    monkeypatch.setattr(
        storage_health,
        "_check_neo4j",
        lambda timeout_seconds: {"uri": "bolt://127.0.0.1:17687"},
    )
    monkeypatch.setattr(
        storage_health,
        "_check_openai",
        lambda timeout_seconds: {"model_probe": "dummy"},
    )
    monkeypatch.setattr(
        storage_health.GLOBAL_BM25,
        "ensure_repo_loaded",
        lambda repo_id: False,
    )

    report = storage_health.run_storage_preflight(
        context="query",
        repo_id="mall",
        force=True,
    )

    bm25_item = next(item for item in report["items"] if item["name"] == "bm25")
    assert report["ok"] is True
    assert report["failed_components"] == []
    assert bm25_item["ok"] is False
    assert bm25_item["critical"] is False
    assert bm25_item["code"] == "bm25_repo_missing"


def test_run_storage_preflight_treats_neo4j_as_non_critical_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No bloquea startup cuando Neo4j falla en preflight."""
    storage_health._CACHE.clear()
    monkeypatch.setattr(storage_health, "get_settings", _fake_settings)
    monkeypatch.setattr(storage_health, "_check_workspace", lambda path: {"path": str(path)})
    monkeypatch.setattr(
        storage_health,
        "_check_metadata_sqlite",
        lambda db_path: {"db_path": str(db_path)},
    )
    monkeypatch.setattr(storage_health, "_check_chroma", lambda: {"collection_count": 0})
    monkeypatch.setattr(
        storage_health,
        "_check_neo4j",
        lambda timeout_seconds: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )
    monkeypatch.setattr(
        storage_health,
        "_check_openai",
        lambda timeout_seconds: {"model_probe": "dummy"},
    )

    report = storage_health.run_storage_preflight(context="startup", force=True)

    neo4j_item = next(item for item in report["items"] if item["name"] == "neo4j")
    assert report["ok"] is True
    assert "neo4j" not in report["failed_components"]
    assert neo4j_item["ok"] is False
    assert neo4j_item["critical"] is False


def test_run_storage_preflight_keeps_neo4j_critical_in_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mantiene Neo4j crítico fuera del contexto startup."""
    storage_health._CACHE.clear()
    monkeypatch.setattr(storage_health, "get_settings", _fake_settings)
    monkeypatch.setattr(storage_health, "_check_workspace", lambda path: {"path": str(path)})
    monkeypatch.setattr(
        storage_health,
        "_check_metadata_sqlite",
        lambda db_path: {"db_path": str(db_path)},
    )
    monkeypatch.setattr(storage_health, "_check_chroma", lambda: {"collection_count": 0})
    monkeypatch.setattr(
        storage_health,
        "_check_neo4j",
        lambda timeout_seconds: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )
    monkeypatch.setattr(
        storage_health,
        "_check_openai",
        lambda timeout_seconds: {"model_probe": "dummy"},
    )
    monkeypatch.setattr(
        storage_health.GLOBAL_BM25,
        "ensure_repo_loaded",
        lambda repo_id: True,
    )

    report = storage_health.run_storage_preflight(
        context="query",
        repo_id="mall",
        force=True,
    )

    neo4j_item = next(item for item in report["items"] if item["name"] == "neo4j")
    assert report["ok"] is False
    assert report["failed_components"] == ["neo4j"]
    assert neo4j_item["ok"] is False
    assert neo4j_item["critical"] is True


def test_run_storage_preflight_treats_workspace_as_non_critical_in_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No bloquea query cuando falla el chequeo del workspace local."""
    storage_health._CACHE.clear()
    monkeypatch.setattr(storage_health, "get_settings", _fake_settings)
    monkeypatch.setattr(
        storage_health,
        "_check_workspace",
        lambda path: (_ for _ in ()).throw(RuntimeError("workspace missing")),
    )
    monkeypatch.setattr(
        storage_health,
        "_check_metadata_sqlite",
        lambda db_path: {"db_path": str(db_path)},
    )
    monkeypatch.setattr(storage_health, "_check_chroma", lambda: {"collection_count": 0})
    monkeypatch.setattr(storage_health, "_check_neo4j", lambda timeout_seconds: {"uri": "bolt://localhost:7687"})
    monkeypatch.setattr(
        storage_health,
        "_check_openai",
        lambda timeout_seconds: {"model_probe": "dummy"},
    )
    monkeypatch.setattr(
        storage_health.GLOBAL_BM25,
        "ensure_repo_loaded",
        lambda repo_id: True,
    )

    report = storage_health.run_storage_preflight(
        context="query",
        repo_id="mall",
        force=True,
    )

    workspace_item = next(item for item in report["items"] if item["name"] == "workspace")
    assert report["ok"] is True
    assert report["failed_components"] == []
    assert workspace_item["ok"] is False
    assert workspace_item["critical"] is False
    assert workspace_item["code"] == "workspace_not_writable"
