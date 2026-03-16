"""Pruebas API para puntos finales primarios."""

import pytest
from fastapi.testclient import TestClient

from coderag.api import server
from coderag.core.storage_health import StoragePreflightError
from coderag.llm.model_discovery import ModelDiscoveryResult

app = server.app


@pytest.fixture(autouse=True)
def bypass_storage_preflight(monkeypatch):
    """Evita dependencia de infraestructura real durante pruebas de API."""

    def fake_ensure_storage_ready(
        *,
        context: str,
        repo_id: str | None = None,
        force: bool = False,
    ) -> dict:
        return {
            "ok": True,
            "strict": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "context": context,
            "repo_id": repo_id,
            "failed_components": [],
            "items": [],
            "cached": force,
        }

    def fake_run_storage_preflight(
        *,
        context: str,
        repo_id: str | None = None,
        force: bool = False,
    ) -> dict:
        return fake_ensure_storage_ready(
            context=context,
            repo_id=repo_id,
            force=force,
        )

    monkeypatch.setattr(server, "ensure_storage_ready", fake_ensure_storage_ready)
    monkeypatch.setattr(server, "run_storage_preflight", fake_run_storage_preflight)


def test_get_missing_job_returns_404() -> None:
    """No se encontraron devoluciones para una identificación de trabajo de ingesta desconocida."""
    client = TestClient(app)
    response = client.get("/jobs/non-existent")
    assert response.status_code == 404


def test_admin_reset_returns_summary(monkeypatch) -> None:
    """Devuelve una carga útil resumida clara cuando la operación de reinicio se realiza correctamente."""

    def fake_reset_all_data() -> tuple[list[str], list[str]]:
        return ["BM25 en memoria", "Grafo Neo4j"], ["warning de prueba"]

    monkeypatch.setattr(server.jobs, "reset_all_data", fake_reset_all_data)
    client = TestClient(app)

    response = client.post("/admin/reset")
    assert response.status_code == 200

    payload = response.json()
    assert payload["message"] == "Limpieza total completada"
    assert "BM25 en memoria" in payload["cleared"]
    assert "warning de prueba" in payload["warnings"]


def test_list_repos_returns_repo_id_catalog(monkeypatch) -> None:
    """Devuelve identificadores de repositorio conocidos para el menú desplegable de consultas."""

    def fake_list_repo_ids() -> list[str]:
        return ["mall", "api-service"]

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    client = TestClient(app)

    response = client.get("/repos")
    assert response.status_code == 200
    assert response.json()["repo_ids"] == ["mall", "api-service"]


def test_provider_models_endpoint_returns_catalog(monkeypatch) -> None:
    """Expone catálogo de modelos por provider para poblar combos de UI."""

    def fake_discover_models(
        provider: str,
        kind: str,
        *,
        force_refresh: bool = False,
    ) -> ModelDiscoveryResult:
        assert provider == "gemini"
        assert kind == "embedding"
        assert force_refresh is True
        return ModelDiscoveryResult(
            provider="gemini",
            kind="embedding",
            models=["text-embedding-004"],
            source="remote",
            warning=None,
        )

    monkeypatch.setattr(server, "discover_models", fake_discover_models)
    client = TestClient(app)

    response = client.get(
        "/providers/models",
        params={
            "provider": "gemini",
            "kind": "embedding",
            "force_refresh": "true",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "gemini"
    assert payload["kind"] == "embedding"
    assert payload["models"] == ["text-embedding-004"]
    assert payload["source"] == "remote"


def test_repo_status_endpoint_returns_structured_repo_readiness(monkeypatch) -> None:
    """Retorna estado consultable por repo con shape estable para UI/API."""

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_query_status(*, repo_id: str, listed_in_catalog: bool) -> dict:
        assert repo_id == "mall"
        assert listed_in_catalog is True
        return {
            "repo_id": "mall",
            "listed_in_catalog": True,
            "query_ready": True,
            "chroma_counts": {
                "code_symbols": 10,
                "code_files": 5,
                "code_modules": 2,
            },
            "bm25_loaded": True,
            "graph_available": True,
            "warnings": [],
        }

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    monkeypatch.setattr(
        server.jobs,
        "get_repo_runtime",
        lambda repo_id: {
            "last_embedding_provider": "gemini",
            "last_embedding_model": "text-embedding-004",
        },
    )
    monkeypatch.setattr(server, "get_repo_query_status", fake_get_repo_query_status)

    client = TestClient(app)
    response = client.get("/repos/mall/status")
    assert response.status_code == 200

    payload = response.json()
    assert payload["repo_id"] == "mall"
    assert payload["listed_in_catalog"] is True
    assert payload["query_ready"] is True
    assert payload["bm25_loaded"] is True
    assert payload["chroma_counts"]["code_symbols"] == 10
    assert payload["last_embedding_provider"] == "gemini"
    assert payload["last_embedding_model"] == "text-embedding-004"


def test_inventory_query_endpoint_returns_paginated_payload(monkeypatch) -> None:
    """Devuelve una respuesta de inventario estructurada a través de un punto final dedicado."""
    from coderag.api import query_service

    def fake_run_inventory_query(
        repo_id: str,
        query: str,
        page: int,
        page_size: int,
    ) -> dict:
        assert repo_id == "mall"
        assert "modelos" in query
        assert page == 2
        assert page_size == 5
        return {
            "answer": "Respuesta inventario",
            "target": "modelo",
            "module_name": "mall-mbg",
            "total": 11,
            "page": 2,
            "page_size": 5,
            "items": [
                {
                    "label": "CmsHelp.java",
                    "path": "mall-mbg/src/main/java/com/macro/mall/model/CmsHelp.java",
                    "kind": "file",
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
            "citations": [
                {
                    "path": "mall-mbg/src/main/java/com/macro/mall/model/CmsHelp.java",
                    "start_line": 1,
                    "end_line": 1,
                    "score": 1.0,
                    "reason": "inventory_graph_match",
                }
            ],
            "diagnostics": {"inventory_count": 11},
        }

    monkeypatch.setattr(query_service, "run_inventory_query", fake_run_inventory_query)
    client = TestClient(app)

    response = client.post(
        "/inventory/query",
        json={
            "repo_id": "mall",
            "query": "cuales son todos los modelos de mall-mbg",
            "page": 2,
            "page_size": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target"] == "modelo"
    assert payload["total"] == 11
    assert payload["page"] == 2
    assert payload["page_size"] == 5
    assert len(payload["items"]) == 1


def test_storage_health_endpoint_returns_structured_payload() -> None:
    """Retorna estado estructurado de salud de almacenamiento."""
    client = TestClient(app)
    response = client.get("/health/storage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["strict"] is True
    assert payload["context"] == "health"
    assert payload["cached"] is True


def test_query_endpoint_forwards_optional_provider_fields(monkeypatch) -> None:
    """Propaga parámetros opcionales de provider/model al servicio run_query."""
    from coderag.api import query_service

    captured: dict[str, object] = {}

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_query_status(*, repo_id: str, listed_in_catalog: bool) -> dict:
        return {
            "repo_id": repo_id,
            "listed_in_catalog": listed_in_catalog,
            "query_ready": True,
            "chroma_counts": {
                "code_symbols": 10,
                "code_files": 3,
                "code_modules": 2,
            },
            "bm25_loaded": True,
            "graph_available": True,
            "warnings": [],
        }

    def fake_run_query(**kwargs):
        captured.update(kwargs)
        return {
            "answer": "ok",
            "citations": [],
            "diagnostics": {},
        }

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    monkeypatch.setattr(server, "get_repo_query_status", fake_get_repo_query_status)
    monkeypatch.setattr(query_service, "run_query", fake_run_query)

    client = TestClient(app)
    response = client.post(
        "/query",
        json={
            "repo_id": "mall",
            "query": "hola",
            "top_n": 5,
            "top_k": 3,
            "embedding_provider": "gemini",
            "embedding_model": "text-embedding-004",
            "llm_provider": "anthropic",
            "answer_model": "claude-3-5-sonnet-20241022",
            "verifier_model": "claude-3-5-sonnet-20241022",
        },
    )

    assert response.status_code == 200
    assert captured["embedding_provider"] == "gemini"
    assert captured["embedding_model"] == "text-embedding-004"
    assert captured["llm_provider"] == "anthropic"
    assert captured["answer_model"] == "claude-3-5-sonnet-20241022"
    assert captured["verifier_model"] == "claude-3-5-sonnet-20241022"


def test_query_endpoint_blocks_when_storage_preflight_fails(monkeypatch) -> None:
    """Bloquea consulta con 503 cuando preflight estricto falla."""

    def fail_preflight(
        *,
        context: str,
        repo_id: str | None = None,
        force: bool = False,
    ) -> dict:
        report = {
            "ok": False,
            "strict": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "context": context,
            "repo_id": repo_id,
            "failed_components": ["neo4j"],
            "items": [],
            "cached": False,
        }
        raise StoragePreflightError(report)

    monkeypatch.setattr(server, "ensure_storage_ready", fail_preflight)
    client = TestClient(app)
    response = client.post(
        "/query",
        json={
            "repo_id": "mall",
            "query": "hola",
            "top_n": 5,
            "top_k": 3,
        },
    )
    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"]["health"]["failed_components"] == ["neo4j"]


def test_query_endpoint_returns_422_when_repo_is_not_ready(monkeypatch) -> None:
    """Cuando el repo no esta listo para query, la API responde 422 con detalle accionable."""

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_query_status(*, repo_id: str, listed_in_catalog: bool) -> dict:
        assert repo_id == "mall"
        assert listed_in_catalog is True
        return {
            "repo_id": "mall",
            "listed_in_catalog": True,
            "query_ready": False,
            "chroma_counts": {
                "code_symbols": 0,
                "code_files": 0,
                "code_modules": 0,
            },
            "bm25_loaded": False,
            "graph_available": None,
            "warnings": ["No hay indice BM25 en memoria para repo 'mall'."],
        }

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    monkeypatch.setattr(server, "get_repo_query_status", fake_get_repo_query_status)

    client = TestClient(app)
    response = client.post(
        "/query",
        json={
            "repo_id": "mall",
            "query": "hola",
            "top_n": 5,
            "top_k": 3,
        },
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["code"] == "repo_not_ready"
    assert payload["detail"]["repo_status"]["query_ready"] is False


@pytest.mark.parametrize(
    "health_code",
    ["neo4j_auth_failed", "neo4j_unreachable"],
)
def test_query_endpoint_exposes_neo4j_failure_code(
    monkeypatch,
    health_code: str,
) -> None:
    """Expone código específico para diferenciar auth inválida vs conexión caída."""

    def fail_preflight(
        *,
        context: str,
        repo_id: str | None = None,
        force: bool = False,
    ) -> dict:
        report = {
            "ok": False,
            "strict": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "context": context,
            "repo_id": repo_id,
            "failed_components": ["neo4j"],
            "items": [
                {
                    "name": "neo4j",
                    "ok": False,
                    "critical": True,
                    "code": health_code,
                    "message": "neo4j failed",
                    "latency_ms": 1.0,
                    "details": {},
                }
            ],
            "cached": False,
        }
        raise StoragePreflightError(report)

    monkeypatch.setattr(server, "ensure_storage_ready", fail_preflight)
    client = TestClient(app)
    response = client.post(
        "/query",
        json={
            "repo_id": "mall",
            "query": "hola",
            "top_n": 5,
            "top_k": 3,
        },
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"]["health"]["items"][0]["name"] == "neo4j"
    assert payload["detail"]["health"]["items"][0]["code"] == health_code


def test_ingest_endpoint_blocks_when_storage_preflight_fails(monkeypatch) -> None:
    """Bloquea ingesta con 503 cuando preflight estricto falla."""

    def fail_preflight(
        *,
        context: str,
        repo_id: str | None = None,
        force: bool = False,
    ) -> dict:
        report = {
            "ok": False,
            "strict": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "context": context,
            "repo_id": repo_id,
            "failed_components": ["chroma"],
            "items": [],
            "cached": False,
        }
        raise StoragePreflightError(report)

    monkeypatch.setattr(server, "ensure_storage_ready", fail_preflight)
    client = TestClient(app)
    response = client.post(
        "/repos/ingest",
        json={
            "provider": "github",
            "repo_url": "https://github.com/acme/mall",
            "branch": "main",
            "commit": None,
            "token": None,
        },
    )
    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"]["health"]["failed_components"] == ["chroma"]
