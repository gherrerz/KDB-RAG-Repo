"""Pruebas API para puntos finales primarios."""

import pytest
from fastapi.testclient import TestClient

import main
from coderag.api import server
from coderag.core.models import JobInfo, JobStatus
from coderag.core.storage_health import StoragePreflightError
from coderag.jobs.worker import IngestionConflictError
from coderag.llm.model_discovery import ModelDiscoveryResult

app = main.app


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


def test_lifespan_startup_succeeds_with_non_critical_neo4j(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valida arranque por lifespan cuando Neo4j falla como check no crítico."""
    observed_contexts: list[str] = []

    def fake_ensure_storage_ready(
        *,
        context: str,
        repo_id: str | None = None,
        force: bool = False,
    ) -> dict:
        observed_contexts.append(context)
        if context == "startup":
            return {
                "ok": True,
                "strict": True,
                "checked_at": "2026-01-01T00:00:00+00:00",
                "context": context,
                "repo_id": repo_id,
                "failed_components": [],
                "items": [
                    {
                        "name": "neo4j",
                        "ok": False,
                        "critical": False,
                        "code": "neo4j_unreachable",
                        "message": "connection refused",
                        "latency_ms": 1.0,
                        "details": {},
                    }
                ],
                "cached": force,
            }
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

    monkeypatch.setattr(server, "ensure_storage_ready", fake_ensure_storage_ready)

    with TestClient(app) as client:
        response = client.get("/repos")
        assert response.status_code == 200
        assert observed_contexts[0] == "startup"
        startup_health = client.app.state.storage_health
        assert startup_health["ok"] is True
        assert startup_health["context"] == "startup"
        assert startup_health["failed_components"] == []
        assert startup_health["items"][0]["name"] == "neo4j"
        assert startup_health["items"][0]["ok"] is False
        assert startup_health["items"][0]["critical"] is False


def test_get_missing_job_returns_404() -> None:
    """No se encontraron devoluciones para una identificación de trabajo de ingesta desconocida."""
    client = TestClient(app)
    response = client.get("/jobs/non-existent")
    assert response.status_code == 404


def test_get_job_supports_logs_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permite acotar logs devueltos para reducir latencia de polling."""

    def fake_get_job(_job_id: str) -> JobInfo:
        return JobInfo(
            id="job-1",
            status=JobStatus.running,
            progress=0.5,
            logs=["l1", "l2", "l3", "l4"],
        )

    monkeypatch.setattr(server.jobs, "get_job", fake_get_job)
    client = TestClient(app)

    response = client.get("/jobs/job-1?logs_tail=2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["logs"] == ["l3", "l4"]
    assert payload["diagnostics"] == {}


def test_get_job_exposes_semantic_diagnostics_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retorna métricas semánticas por tipo dentro de diagnostics en jobs."""

    def fake_get_job(_job_id: str) -> JobInfo:
        return JobInfo(
            id="job-2",
            status=JobStatus.completed,
            progress=1.0,
            logs=["Ingesta finalizada"],
            diagnostics={
                "semantic_graph": {
                    "enabled": True,
                    "status": "ok",
                    "relation_counts": 7,
                    "relation_counts_by_type": {
                        "CALLS": 4,
                        "IMPORTS": 2,
                        "EXTENDS": 1,
                    },
                    "java_cross_file_resolved_count": 3,
                    "java_cross_file_resolved_by_type": {
                        "CALLS": 2,
                        "IMPLEMENTS": 1,
                    },
                    "java_resolution_source_counts": {
                        "import": 2,
                        "static_import_member": 1,
                        "same_package": 1,
                    },
                    "unresolved_count": 2,
                    "unresolved_by_type": {
                        "IMPORTS": 2,
                    },
                    "unresolved_ratio": 0.2857,
                    "semantic_extraction_ms": 19.4,
                }
            },
        )

    monkeypatch.setattr(server.jobs, "get_job", fake_get_job)
    client = TestClient(app)

    response = client.get("/jobs/job-2")
    assert response.status_code == 200
    payload = response.json()
    semantic = payload["diagnostics"]["semantic_graph"]

    assert semantic["relation_counts_by_type"] == {
        "CALLS": 4,
        "IMPORTS": 2,
        "EXTENDS": 1,
    }
    assert semantic["java_cross_file_resolved_count"] == 3
    assert semantic["java_cross_file_resolved_by_type"] == {
        "CALLS": 2,
        "IMPLEMENTS": 1,
    }
    assert semantic["java_resolution_source_counts"] == {
        "import": 2,
        "static_import_member": 1,
        "same_package": 1,
    }
    assert semantic["unresolved_by_type"] == {"IMPORTS": 2}


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


def test_delete_repo_returns_summary(monkeypatch) -> None:
    """Devuelve resumen detallado al eliminar un repositorio puntual."""

    def fake_delete_repo(repo_id: str) -> tuple[list[str], list[str], dict[str, int]]:
        assert repo_id == "mall"
        return (
            ["Chroma", "BM25", "Grafo Neo4j", "Workspace", "Metadata SQLite"],
            ["warning de prueba"],
            {
                "chroma_total": 12,
                "bm25_docs": 4,
                "neo4j_nodes": 9,
                "workspace_dirs": 1,
                "metadata_total": 3,
            },
        )

    monkeypatch.setattr(server.jobs, "delete_repo", fake_delete_repo)
    client = TestClient(app)

    response = client.delete("/repos/mall")
    assert response.status_code == 200

    payload = response.json()
    assert payload["repo_id"] == "mall"
    assert payload["message"] == "Repositorio 'mall' eliminado"
    assert "Chroma" in payload["cleared"]
    assert payload["deleted_counts"]["neo4j_nodes"] == 9
    assert payload["warnings"] == ["warning de prueba"]


def test_delete_repo_returns_404_when_missing(monkeypatch) -> None:
    """Devuelve 404 cuando el repo_id no existe en el catálogo."""

    def fake_delete_repo(repo_id: str) -> tuple[list[str], list[str], dict[str, int]]:
        raise LookupError(f"repo '{repo_id}' no existe")

    monkeypatch.setattr(server.jobs, "delete_repo", fake_delete_repo)
    client = TestClient(app)

    response = client.delete("/repos/nope")
    assert response.status_code == 404
    assert "no existe" in str(response.json().get("detail", "")).lower()


def test_delete_repo_returns_409_when_same_repo_job_running(monkeypatch) -> None:
    """Devuelve 409 cuando hay una ingesta activa para el mismo repo_id."""

    def fake_delete_repo(_repo_id: str) -> tuple[list[str], list[str], dict[str, int]]:
        raise RuntimeError("ingesta activa para el mismo repositorio")

    monkeypatch.setattr(server.jobs, "delete_repo", fake_delete_repo)
    client = TestClient(app)

    response = client.delete("/repos/mall")
    assert response.status_code == 409
    assert "ingesta activa" in str(response.json().get("detail", "")).lower()


def test_list_repos_returns_repo_id_catalog(monkeypatch) -> None:
    """Devuelve ids y metadata básica de repositorios conocidos para consultas."""

    def fake_list_repo_catalog() -> list[dict[str, str | None]]:
        return [
            {
                "repo_id": "api-service",
                "url": None,
                "branch": None,
            },
            {
                "repo_id": "mall",
                "url": "https://github.com/macrozheng/mall.git",
                "branch": "main",
            },
        ]

    monkeypatch.setattr(server.jobs, "list_repo_catalog", fake_list_repo_catalog)
    client = TestClient(app)

    response = client.get("/repos")
    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_ids"] == ["api-service", "mall"]
    assert payload["repositories"] == [
        {
            "repo_id": "api-service",
            "organization": None,
            "url": None,
            "branch": None,
        },
        {
            "repo_id": "mall",
            "organization": "macrozheng",
            "url": "https://github.com/macrozheng/mall.git",
            "branch": "main",
        },
    ]


def test_ingest_repo_returns_503_when_enqueue_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retorna 503 cuando no es posible iniciar el job asíncrono."""

    def fake_create_ingest_job(_request) -> JobInfo:
        raise RuntimeError("No se pudo encolar en Redis")

    monkeypatch.setattr(server.jobs, "create_ingest_job", fake_create_ingest_job)
    client = TestClient(app)

    response = client.post(
        "/repos/ingest",
        json={
            "provider": "github",
            "repo_url": "https://github.com/acme/fail.git",
            "branch": "main",
        },
    )

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["message"] == "No se pudo iniciar la ingesta asíncrona."
    assert "encolar" in payload["error"].lower()


def test_ingest_repo_returns_409_when_same_repo_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retorna 409 cuando ya existe ingesta activa del mismo repositorio."""

    def fake_create_ingest_job(_request) -> JobInfo:
        raise IngestionConflictError("Ya existe una ingesta activa para 'mall'.")

    monkeypatch.setattr(server.jobs, "create_ingest_job", fake_create_ingest_job)
    client = TestClient(app)

    response = client.post(
        "/repos/ingest",
        json={
            "provider": "github",
            "repo_url": "https://github.com/acme/mall.git",
            "branch": "main",
        },
    )

    assert response.status_code == 409
    payload = response.json()["detail"]
    assert payload["message"] == "Ya existe una ingesta activa para el repositorio."
    assert "ingesta activa" in payload["error"].lower()


def test_ingest_repo_normalizes_swagger_placeholder_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignora el placeholder `string` de Swagger en commit opcional."""

    def fake_create_ingest_job(request) -> JobInfo:
        assert request.commit is None
        return JobInfo(id="job-123", status=JobStatus.queued, repo_id="mall")

    monkeypatch.setattr(server.jobs, "create_ingest_job", fake_create_ingest_job)
    client = TestClient(app)

    response = client.post(
        "/repos/ingest",
        json={
            "provider": "bitbucket",
            "repo_url": "https://bitbucket.org/acme/mall.git",
            "branch": "main",
            "commit": "string",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "job-123"


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

    def fake_get_repo_query_status(
        *,
        repo_id: str,
        listed_in_catalog: bool,
        runtime_payload: dict | None = None,
        requested_embedding_provider: str | None = None,
        requested_embedding_model: str | None = None,
    ) -> dict:
        assert repo_id == "mall"
        assert listed_in_catalog is True
        assert runtime_payload is not None
        assert requested_embedding_provider is None
        assert requested_embedding_model is None
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
    response = client.get("/health")
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

    def fake_get_repo_query_status(
        *,
        repo_id: str,
        listed_in_catalog: bool,
        runtime_payload: dict | None = None,
        requested_embedding_provider: str | None = None,
        requested_embedding_model: str | None = None,
    ) -> dict:
        assert runtime_payload is None
        assert requested_embedding_provider == "gemini"
        assert requested_embedding_model == "text-embedding-004"
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


def test_retrieval_query_endpoint_returns_structured_payload(monkeypatch) -> None:
    """Expone respuesta retrieval-only estructurada y sin síntesis LLM."""
    from coderag.api import query_service

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_query_status(**kwargs) -> dict:  # noqa: ANN003
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

    def fake_run_retrieval_query(**kwargs):
        assert kwargs["include_context"] is True
        return {
            "mode": "retrieval_only",
            "answer": "Modo retrieval-only (sin LLM)",
            "chunks": [
                {
                    "id": "a1",
                    "text": "class AuthService {}",
                    "score": 0.9,
                    "path": "src/AuthService.java",
                    "start_line": 10,
                    "end_line": 20,
                    "kind": "code_chunk",
                    "metadata": {"path": "src/AuthService.java"},
                }
            ],
            "citations": [
                {
                    "path": "src/AuthService.java",
                    "start_line": 10,
                    "end_line": 20,
                    "score": 0.9,
                    "reason": "hybrid_rag_match",
                }
            ],
            "statistics": {
                "total_before_rerank": 1,
                "total_after_rerank": 1,
                "graph_nodes_count": 0,
            },
            "diagnostics": {"retrieved": 1, "reranked": 1},
            "context": "PATH: src/AuthService.java",
        }

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    monkeypatch.setattr(server, "get_repo_query_status", fake_get_repo_query_status)
    monkeypatch.setattr(query_service, "run_retrieval_query", fake_run_retrieval_query)

    client = TestClient(app)
    response = client.post(
        "/query/retrieval",
        json={
            "repo_id": "mall",
            "query": "auth service",
            "top_n": 10,
            "top_k": 5,
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-small",
            "include_context": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "retrieval_only"
    assert len(payload["chunks"]) == 1
    assert payload["statistics"]["total_after_rerank"] == 1


def test_retrieval_query_endpoint_forwards_embedding_and_context_flags(monkeypatch) -> None:
    """Propaga provider/model/include_context al servicio retrieval-only."""
    from coderag.api import query_service

    captured: dict[str, object] = {}

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_query_status(
        *,
        repo_id: str,
        listed_in_catalog: bool,
        runtime_payload: dict | None = None,
        requested_embedding_provider: str | None = None,
        requested_embedding_model: str | None = None,
    ) -> dict:
        assert repo_id == "mall"
        assert listed_in_catalog is True
        assert runtime_payload is None
        assert requested_embedding_provider == "gemini"
        assert requested_embedding_model == "text-embedding-004"
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

    def fake_run_retrieval_query(**kwargs):
        captured.update(kwargs)
        return {
            "mode": "retrieval_only",
            "answer": "ok",
            "chunks": [],
            "citations": [],
            "statistics": {
                "total_before_rerank": 0,
                "total_after_rerank": 0,
                "graph_nodes_count": 0,
            },
            "diagnostics": {},
            "context": None,
        }

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    monkeypatch.setattr(server, "get_repo_query_status", fake_get_repo_query_status)
    monkeypatch.setattr(query_service, "run_retrieval_query", fake_run_retrieval_query)

    client = TestClient(app)
    response = client.post(
        "/query/retrieval",
        json={
            "repo_id": "mall",
            "query": "hola",
            "top_n": 5,
            "top_k": 3,
            "embedding_provider": "gemini",
            "embedding_model": "text-embedding-004",
            "include_context": True,
        },
    )

    assert response.status_code == 200
    assert captured["embedding_provider"] == "gemini"
    assert captured["embedding_model"] == "text-embedding-004"
    assert captured["include_context"] is True


def test_retrieval_query_endpoint_returns_422_when_repo_not_ready(monkeypatch) -> None:
    """Responde 422 cuando el repo no está listo para retrieval-only."""

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_query_status(**kwargs) -> dict:  # noqa: ANN003
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
        "/query/retrieval",
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


def test_retrieval_query_endpoint_blocks_when_storage_preflight_fails(monkeypatch) -> None:
    """Bloquea retrieval-only con 503 cuando preflight estricto falla."""

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
        "/query/retrieval",
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


def test_query_endpoint_blocks_when_chroma_space_preflight_fails(monkeypatch) -> None:
    """Bloquea consulta cuando preflight detecta mismatch de hnsw.space en Chroma."""

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
            "items": [
                {
                    "name": "chroma",
                    "ok": False,
                    "critical": True,
                    "code": "chroma_hnsw_space_mismatch",
                    "message": "Espacio HNSW inconsistente en Chroma.",
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
    assert payload["detail"]["health"]["failed_components"] == ["chroma"]


def test_query_endpoint_returns_422_when_repo_is_not_ready(monkeypatch) -> None:
    """Cuando el repo no esta listo para query, la API responde 422 con detalle accionable."""

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_query_status(
        *,
        repo_id: str,
        listed_in_catalog: bool,
        runtime_payload: dict | None = None,
        requested_embedding_provider: str | None = None,
        requested_embedding_model: str | None = None,
    ) -> dict:
        assert repo_id == "mall"
        assert listed_in_catalog is True
        assert runtime_payload is None
        assert requested_embedding_provider is None
        assert requested_embedding_model is None
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


def test_query_endpoint_returns_422_when_embedding_is_incompatible(monkeypatch) -> None:
    """Devuelve error explícito cuando embedding de consulta no es compatible con la ingesta."""

    def fake_list_repo_ids() -> list[str]:
        return ["mall"]

    def fake_get_repo_runtime(repo_id: str) -> dict[str, str]:
        assert repo_id == "mall"
        return {
            "last_embedding_provider": "openai",
            "last_embedding_model": "text-embedding-3-small",
        }

    def fake_get_repo_query_status(
        *,
        repo_id: str,
        listed_in_catalog: bool,
        runtime_payload: dict | None = None,
        requested_embedding_provider: str | None = None,
        requested_embedding_model: str | None = None,
    ) -> dict:
        assert repo_id == "mall"
        assert listed_in_catalog is True
        assert runtime_payload is not None
        assert requested_embedding_provider == "vertex_ai"
        assert requested_embedding_model == "text-embedding-005"
        return {
            "repo_id": "mall",
            "listed_in_catalog": True,
            "query_ready": False,
            "chroma_counts": {
                "code_symbols": 10,
                "code_files": 10,
                "code_modules": 4,
            },
            "bm25_loaded": True,
            "graph_available": True,
            "embedding_compatible": False,
            "compatibility_reason": "embedding_dimension_mismatch",
            "warnings": [
                "El modelo/provider de embeddings de consulta no es compatible con la última ingesta del repositorio."
            ],
        }

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    monkeypatch.setattr(server.jobs, "get_repo_runtime", fake_get_repo_runtime)
    monkeypatch.setattr(server, "get_repo_query_status", fake_get_repo_query_status)

    client = TestClient(app)
    response = client.post(
        "/query",
        json={
            "repo_id": "mall",
            "query": "cuales son las dependencias del proyecto",
            "top_n": 5,
            "top_k": 3,
            "embedding_provider": "vertex_ai",
            "embedding_model": "text-embedding-005",
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["code"] == "embedding_incompatible"
    assert payload["detail"]["repo_status"]["embedding_compatible"] is False


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
        },
    )
    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"]["health"]["failed_components"] == ["chroma"]
