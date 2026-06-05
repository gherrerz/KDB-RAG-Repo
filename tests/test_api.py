"""Pruebas API para puntos finales primarios."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from coderag.api import server
from coderag.core.models import JobInfo, JobStatus
from coderag.core.storage_health import StoragePreflightError
from coderag.jobs.worker import IngestionConflictError, JobManager
from coderag.llm.model_discovery import ModelDiscoveryResult
from coderag.storage.metadata_store import MetadataStore

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
    monkeypatch.setattr(
        server,
        "ensure_postgres_schema_ready",
        lambda settings: {
            "enabled": False,
            "policy": "auto_upgrade",
            "action": "skipped",
            "current_heads": [],
            "expected_heads": [],
            "cached": False,
        },
    )
    monkeypatch.setattr(
        server.jobs,
        "touch_repo_last_queried_at",
        lambda repo_id: 1,
    )
    monkeypatch.setattr(
        server.jobs,
        "get_repo_runtime",
        lambda repo_id: None,
    )
    monkeypatch.setattr(
        server.jobs,
        "list_stale_repos",
        lambda **kwargs: [],
    )


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
    observed_bootstrap_calls: list[object] = []
    monkeypatch.setattr(
        server,
        "ensure_postgres_schema_ready",
        lambda settings: observed_bootstrap_calls.append(settings) or {
            "enabled": False,
            "policy": "auto_upgrade",
            "action": "skipped",
            "current_heads": [],
            "expected_heads": [],
            "cached": False,
        },
    )

    with TestClient(app) as client:
        response = client.get("/repos")
        assert response.status_code == 200
        assert len(observed_bootstrap_calls) == 1
        assert observed_contexts[0] == "startup"
        startup_health = client.app.state.storage_health
        assert client.app.state.postgres_startup["action"] == "skipped"
        assert startup_health["postgres_startup"]["action"] == "skipped"
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


def test_get_job_uses_state_job_manager_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permite resolver el gestor desde app.state para facilitar overrides en tests."""

    class FakeJobManager:
        def get_job(self, _job_id: str) -> JobInfo | None:
            return JobInfo(
                id="job-from-state",
                status=JobStatus.completed,
                progress=1.0,
                logs=["override"],
            )

    monkeypatch.setattr(
        server.app.state,
        "job_manager_override",
        FakeJobManager(),
        raising=False,
    )
    client = TestClient(app)

    response = client.get("/jobs/job-from-state")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "job-from-state"
    assert payload["logs"] == ["override"]


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
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            admin_reset_enabled=True,
            admin_reset_token="secret-token",
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/admin/reset",
        headers={"X-Admin-Reset-Token": "secret-token"},
        json={
            "confirm": True,
            "confirmation_phrase": "RESET ALL DATA",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["message"] == "Limpieza total completada"
    assert "BM25 en memoria" in payload["cleared"]
    assert "warning de prueba" in payload["warnings"]


def test_admin_reset_is_disabled_by_feature_flag(monkeypatch) -> None:
    """Oculta el endpoint de reset cuando el flag administrativo está apagado."""

    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            admin_reset_enabled=False,
            admin_reset_token="secret-token",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/reset",
        headers={"X-Admin-Reset-Token": "secret-token"},
        json={
            "confirm": True,
            "confirmation_phrase": "RESET ALL DATA",
        },
    )

    assert response.status_code == 404


def test_admin_reset_requires_valid_admin_token(monkeypatch) -> None:
    """Requiere token administrativo válido cuando el reset está habilitado."""

    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            admin_reset_enabled=True,
            admin_reset_token="secret-token",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/reset",
        json={
            "confirm": True,
            "confirmation_phrase": "RESET ALL DATA",
        },
    )

    assert response.status_code == 403


def test_admin_reset_rejects_invalid_confirmation_phrase(monkeypatch) -> None:
    """Rechaza payloads con confirmación humana distinta a la frase requerida."""

    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            admin_reset_enabled=True,
            admin_reset_token="secret-token",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/reset",
        headers={"X-Admin-Reset-Token": "secret-token"},
        json={
            "confirm": True,
            "confirmation_phrase": "RESET DATA",
        },
    )

    assert response.status_code == 422


def test_admin_reset_openapi_marks_header_as_required(monkeypatch) -> None:
    """Publica el header administrativo del reset como requerido en OpenAPI."""

    app.openapi_schema = None
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    parameters = payload["paths"]["/admin/reset"]["post"]["parameters"]
    header_parameter = next(
        parameter
        for parameter in parameters
        if parameter["name"] == "X-Admin-Reset-Token"
        and parameter["in"] == "header"
    )
    assert header_parameter["required"] is True


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


def test_delete_repo_returns_200_after_reconciling_orphan_job(
    monkeypatch,
    tmp_path,
    patch_module_settings,
) -> None:
    """Devuelve 200 cuando el manager reconcilia un job zombie del repo."""

    import coderag.jobs.worker as worker_module
    import coderag.maintenance.reset_service as reset_module

    patch_module_settings(worker_module)

    manager = JobManager()
    manager.store = MetadataStore(tmp_path / "metadata.db")

    orphan = JobInfo(
        id="job-zombie",
        status=JobStatus.running,
        progress=0.5,
        logs=["Procesando repo..."],
        repo_id="mall",
        error=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    manager.store.upsert_job(orphan)

    monkeypatch.setattr(
        reset_module,
        "delete_repo_storage",
        lambda repo_id: ([f"repo={repo_id}"], [], {"metadata_total": 1}),
    )
    monkeypatch.setattr(app.state, "job_manager_override", manager, raising=False)

    client = TestClient(app)
    response = client.delete("/repos/mall")

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_id"] == "mall"
    assert payload["cleared"] == ["repo=mall"]

    recovered = manager.store.get_job(orphan.id)
    assert recovered is not None
    assert recovered.status == JobStatus.failed
    assert recovered.diagnostics["orphan_reconciled"] is True


def test_list_repos_returns_repo_id_catalog(monkeypatch) -> None:
    """Devuelve ids y metadata básica de repositorios conocidos para consultas."""

    def fake_list_repo_catalog() -> list[dict[str, str | None]]:
        return [
            {
                "repo_id": "api-service",
                "organization": None,
                "url": None,
                "branch": None,
            },
            {
                "repo_id": "macrozheng-mall-main",
                "organization": "macrozheng",
                "url": "https://github.com/macrozheng/mall.git",
                "branch": "main",
            },
        ]

    monkeypatch.setattr(server.jobs, "list_repo_catalog", fake_list_repo_catalog)
    client = TestClient(app)

    response = client.get("/repos")
    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_ids"] == ["api-service", "macrozheng-mall-main"]
    assert payload["repositories"] == [
        {
            "repo_id": "api-service",
            "organization": None,
            "url": None,
            "branch": None,
        },
        {
            "repo_id": "macrozheng-mall-main",
            "organization": "macrozheng",
            "url": "https://github.com/macrozheng/mall.git",
            "branch": "main",
        },
    ]


def test_list_repo_snapshots_returns_operational_history(monkeypatch) -> None:
    """Devuelve snapshots operativos persistidos para un repositorio conocido."""

    def fake_list_repo_ingest_snapshots(
        repo_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        assert repo_id == "mall"
        assert limit == 5
        return [
            {
                "snapshot_id": 9,
                "repo_id": "mall",
                "job_id": "job-9",
                "snapshot_at": "2026-05-23T12:00:00+00:00",
                "job_status": "completed",
                "error_message": None,
                "retryable_error": False,
                "workspace_retained": True,
                "workspace_cleanup_attempted": False,
                "workspace_cleanup_succeeded": False,
                "clone_ms": 10.0,
                "scan_ms": 11.0,
                "chunk_ms": 12.0,
                "vector_total_ms": 13.0,
                "lexical_ms": 14.0,
                "graph_ms": 15.0,
                "readiness_ms": 16.0,
                "ingestion_total_ms": 17.0,
                "files_visited": 100,
                "files_scanned": 80,
                "excluded_dir_count": 1,
                "excluded_extension_count": 2,
                "excluded_file_count": 3,
                "excluded_size_count": 4,
                "excluded_decode_count": 5,
                "excluded_pattern_count": 6,
                "visited_dirs": 10,
                "pruned_dirs": 2,
                "symbols_count": 30,
                "chunks_count": 40,
                "languages_detected_count": 3,
                "vector_collections_written": 3,
                "vector_initial_batch_size": 100,
                "vector_effective_batch_size": 50,
                "vector_split_count": 2,
                "vector_recovered_retry_count": 1,
                "vector_payload_too_large_events": 1,
                "vector_proxy_reset_events": 0,
                "vector_upstream_restarting_events": 0,
                "vector_documents_written": 120,
                "semantic_enabled": True,
                "semantic_status": "ok",
                "semantic_relations_count": 7,
                "semantic_unresolved_count": 1,
            }
        ]

    monkeypatch.setattr(server.jobs, "list_repo_ids", lambda: ["mall"])
    monkeypatch.setattr(
        server.jobs,
        "list_repo_ingest_snapshots",
        fake_list_repo_ingest_snapshots,
    )
    client = TestClient(app)

    response = client.get("/repos/mall/snapshots?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_id"] == "mall"
    assert payload["snapshots"][0]["snapshot_id"] == 9
    assert payload["snapshots"][0]["vector_documents_written"] == 120


def test_list_repo_snapshots_returns_404_for_unknown_repo(monkeypatch) -> None:
    """Retorna 404 cuando el repo no existe y tampoco tiene snapshots persistidos."""

    monkeypatch.setattr(server.jobs, "list_repo_ids", lambda: [])
    monkeypatch.setattr(
        server.jobs,
        "list_repo_ingest_snapshots",
        lambda repo_id, limit=20: [],
    )
    client = TestClient(app)

    response = client.get("/repos/unknown/snapshots")

    assert response.status_code == 404
    assert response.json()["detail"] == "Repositorio no encontrado: unknown"


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


def test_api_end_to_end_ingest_cleanup_then_status_and_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Recorre ingesta, cleanup post-ingesta y posterior status/query sin workspace."""

    class _Settings:
        workspace_path = tmp_path / "workspace"
        retain_workspace_after_ingest = False
        ingestion_execution_mode = "thread"

    _Settings.workspace_path.mkdir(parents=True, exist_ok=True)

    import coderag.jobs.worker as worker_module
    import coderag.ingestion.pipeline as pipeline_module
    import coderag.core.storage_health as health_module
    from coderag.api import query_service

    monkeypatch.setattr(worker_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        worker_module,
        "_build_metadata_store",
        lambda: MetadataStore(tmp_path / "metadata.db"),
    )
    manager = JobManager()
    monkeypatch.setattr(server, "jobs", manager)

    class _SyncThread:
        def __init__(self, target, args, daemon):
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(worker_module, "Thread", _SyncThread)

    def _fake_ingest_repository(repo_url, branch, commit, logger, **kwargs) -> str:
        del repo_url, branch, commit, logger, kwargs
        repo_path = _Settings.workspace_path / "acme-demo-main"
        repo_path.mkdir(parents=True, exist_ok=True)
        (repo_path / "README.md").write_text("demo\n", encoding="utf-8")
        return "acme-demo-main"

    def _fake_repo_query_status(
        *,
        repo_id: str,
        listed_in_catalog: bool,
        runtime_payload: dict | None = None,
        requested_embedding_provider: str | None = None,
        requested_embedding_model: str | None = None,
    ) -> dict:
        assert repo_id == "acme-demo-main"
        assert listed_in_catalog is True
        assert not (_Settings.workspace_path / repo_id).exists()
        _ = requested_embedding_provider, requested_embedding_model
        return {
            "repo_id": repo_id,
            "listed_in_catalog": listed_in_catalog,
            "workspace_available": False,
            "query_ready": True,
            "chroma_counts": {
                "code_symbols": 3,
                "code_files": 1,
                "code_modules": 1,
            },
            "lexical_loaded": True,
            "graph_available": True,
            "embedding_compatible": True,
            "compatibility_reason": "compatible",
            "warnings": [],
        }

    def _fake_run_query(**kwargs):
        assert kwargs["repo_id"] == "acme-demo-main"
        return {
            "answer": "respuesta semántica sin workspace local",
            "citations": [
                {
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 1,
                    "score": 1.0,
                    "reason": "hybrid_rag_match",
                }
            ],
            "diagnostics": {
                "inventory_intent": False,
                "inventory_route": None,
            },
        }

    monkeypatch.setattr(pipeline_module, "ingest_repository", _fake_ingest_repository)
    monkeypatch.setattr(health_module, "get_repo_query_status", _fake_repo_query_status)
    monkeypatch.setattr(server, "get_repo_query_status", _fake_repo_query_status)
    monkeypatch.setattr(query_service, "run_query", _fake_run_query)

    client = TestClient(app)
    ingest_response = client.post(
        "/repos/ingest",
        json={
            "provider": "github",
            "repo_url": "https://github.com/acme/demo.git",
            "branch": "main",
        },
    )

    assert ingest_response.status_code == 200
    ingest_payload = ingest_response.json()
    assert ingest_payload["repo_id"] == "acme-demo-main"
    assert ingest_payload["status"] == "completed"

    job_response = client.get(f"/jobs/{ingest_payload['id']}")
    assert job_response.status_code == 200
    job_payload = job_response.json()
    assert job_payload["diagnostics"]["workspace_retained"] is False
    assert job_payload["diagnostics"]["workspace_cleanup_succeeded"] is True

    status_response = client.get("/repos/acme-demo-main/status")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["workspace_available"] is False
    assert status_payload["query_ready"] is True
    assert status_payload["last_embedding_provider"] == "vertex"
    assert status_payload["last_embedding_model"] == "text-embedding-005"

    query_response = client.post(
        "/query",
        json={
            "repo_id": "acme-demo-main",
            "query": "resume el repositorio",
            "top_n": 5,
            "top_k": 3,
        },
    )
    assert query_response.status_code == 200
    assert query_response.json()["answer"] == "respuesta semántica sin workspace local"


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
            "workspace_available": False,
            "query_ready": True,
            "chroma_counts": {
                "code_symbols": 10,
                "code_files": 5,
                "code_modules": 2,
            },
            "lexical_loaded": True,
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
    assert payload["workspace_available"] is False
    assert payload["query_ready"] is True
    assert payload["lexical_loaded"] is True
    assert "bm25_loaded" not in payload
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


def test_query_endpoint_marks_repo_as_queried_before_delegating(monkeypatch) -> None:
    """Marca la última consulta del repo cuando /query entra a flujo válido."""
    from coderag.api import query_service

    touched: list[str] = []

    monkeypatch.setattr(server.jobs, "list_repo_ids", lambda: ["mall"])
    monkeypatch.setattr(server.jobs, "get_repo_runtime", lambda repo_id: None)
    monkeypatch.setattr(server.jobs, "touch_repo_last_queried_at", touched.append)
    monkeypatch.setattr(
        server,
        "get_repo_query_status",
        lambda **kwargs: {
            "repo_id": "mall",
            "listed_in_catalog": True,
            "query_ready": True,
            "chroma_counts": {
                "code_symbols": 1,
                "code_files": 1,
                "code_modules": 1,
            },
            "lexical_loaded": True,
            "graph_available": True,
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        query_service,
        "run_query",
        lambda **kwargs: {"answer": "ok", "citations": [], "diagnostics": {}},
    )

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

    assert response.status_code == 200
    assert touched == ["mall"]


def test_inventory_query_endpoint_marks_repo_as_queried(monkeypatch) -> None:
    """Marca la última consulta del repo al entrar al flujo de inventario."""
    from coderag.api import query_service

    touched: list[str] = []
    monkeypatch.setattr(server.jobs, "touch_repo_last_queried_at", touched.append)
    monkeypatch.setattr(
        query_service,
        "run_inventory_query",
        lambda **kwargs: {
            "answer": "ok",
            "target": "modelo",
            "module_name": None,
            "total": 0,
            "page": 1,
            "page_size": 5,
            "items": [],
            "citations": [],
            "diagnostics": {},
        },
    )

    client = TestClient(app)
    response = client.post(
        "/inventory/query",
        json={
            "repo_id": "mall",
            "query": "cuales son todos los modelos",
            "page": 1,
            "page_size": 5,
        },
    )

    assert response.status_code == 200
    assert touched == ["mall"]


def test_retrieval_query_endpoint_marks_repo_as_queried(monkeypatch) -> None:
    """Marca la última consulta del repo al entrar a retrieval-only válido."""
    from coderag.api import query_service

    touched: list[str] = []
    monkeypatch.setattr(server.jobs, "list_repo_ids", lambda: ["mall"])
    monkeypatch.setattr(server.jobs, "get_repo_runtime", lambda repo_id: None)
    monkeypatch.setattr(server.jobs, "touch_repo_last_queried_at", touched.append)
    monkeypatch.setattr(
        server,
        "get_repo_query_status",
        lambda **kwargs: {
            "repo_id": "mall",
            "listed_in_catalog": True,
            "query_ready": True,
            "chroma_counts": {
                "code_symbols": 1,
                "code_files": 1,
                "code_modules": 1,
            },
            "lexical_loaded": True,
            "graph_available": True,
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        query_service,
        "run_retrieval_query",
        lambda **kwargs: {
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
        },
    )

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

    assert response.status_code == 200
    assert touched == ["mall"]


def test_list_stale_repos_endpoint_returns_runtime_payload(monkeypatch) -> None:
    """Expone repositorios stale con el detalle runtime solicitado."""
    cutoff = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        server.jobs,
        "list_stale_repos",
        lambda **kwargs: [
            {
                "repo_id": "mall",
                "organization": "macrozheng",
                "url": "https://github.com/macrozheng/mall.git",
                "branch": "main",
                "local_path": "/workspace/mall",
                "created_at": datetime(2026, 4, 1, 0, 0, tzinfo=UTC),
                "updated_at": datetime(2026, 4, 2, 0, 0, tzinfo=UTC),
                "last_queried_at": None,
            }
        ],
    )

    client = TestClient(app)
    response = client.get(
        "/repos/last-query/stale",
        params={"last_queried_on_or_before": cutoff.isoformat()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["last_queried_on_or_before"] == "2026-05-01T00:00:00Z"
    assert payload["repositories"] == [
        {
            "repo_id": "mall",
            "organization": "macrozheng",
            "url": "https://github.com/macrozheng/mall.git",
            "branch": "main",
            "local_path": "/workspace/mall",
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-02T00:00:00Z",
            "last_queried_at": None,
        }
    ]


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
    assert payload["postgres_startup"]["action"] == "skipped"
    assert payload["postgres_startup"]["policy"] == "auto_upgrade"


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
            "lexical_loaded": True,
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
            "lexical_loaded": True,
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
            "lexical_loaded": True,
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
            "graph_available": None,
            "warnings": [
                "No hay corpus léxico en Postgres para repo 'mall'."
            ],
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
        assert requested_embedding_provider == "vertex"
        assert requested_embedding_model == "text-embedding-005"
        return {
            "repo_id": "mall",
            "listed_in_catalog": True,
            "query_ready": False,
            "chroma_counts": {
                "code_symbols": 0,
                "code_files": 0,
                "code_modules": 0,
            },
            "lexical_loaded": False,
            "graph_available": None,
            "warnings": [
                "No hay corpus léxico en Postgres para repo 'mall'."
            ],
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
    assert payload["detail"]["repo_status"]["lexical_loaded"] is False
    assert "bm25_loaded" not in payload["detail"]["repo_status"]


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
        assert requested_embedding_provider == "vertex"
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
            "lexical_loaded": True,
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
            "embedding_provider": "vertex",
            "embedding_model": "text-embedding-005",
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["code"] == "embedding_incompatible"
    assert payload["detail"]["repo_status"]["embedding_compatible"] is False


@pytest.mark.parametrize(
    "health_code",
    [
        "neo4j_auth_failed",
        "neo4j_timeout",
        "neo4j_dns_failed",
        "neo4j_tls_failed",
        "neo4j_unreachable",
    ],
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


def test_chroma_diagnostics_endpoint_returns_counts(monkeypatch) -> None:
    """Retorna conteos y metadata para colecciones gestionadas de Chroma."""

    class FakeIndex:
        def list_collection_names(self) -> list[str]:
            return ["code_symbols", "code_files"]

        def count_collection(
            self,
            collection_name: str,
            page_size: int = 500,
            where: dict | None = None,
        ) -> int:
            assert page_size == 250
            assert where is None
            return {"code_symbols": 11, "code_files": 3}[collection_name]

        def get_collection_metadata(self, collection_name: str) -> dict[str, str]:
            return {"name": collection_name, "hnsw:space": "cosine"}

        def count_by_repo_id(
            self,
            collection_name: str,
            repo_id: str,
            page_size: int = 500,
        ) -> int:
            assert repo_id == "mall"
            assert page_size == 250
            return {"code_symbols": 7, "code_files": 1}[collection_name]

    monkeypatch.setattr(server, "build_managed_vector_index", lambda: FakeIndex())
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            chroma_mode="remote",
            chroma_admin_api_enabled=True,
            chroma_admin_api_token="",
        ),
    )

    client = TestClient(app)
    response = client.get(
        "/admin/chroma/diagnostics",
        params=[
            ("repo_id", "mall"),
            ("collection_names", "code_symbols"),
            ("page_size", "250"),
        ],
        headers={"X-Chroma-Admin-Token": "ignored-when-empty"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chroma_mode"] == "remote"
    assert payload["repo_id"] == "mall"
    assert payload["partial"] is False
    assert payload["collection_names"] == ["code_symbols"]
    assert payload["collections"][0]["total_count"] == 11
    assert payload["collections"][0]["repo_count"] == 7
    assert payload["collections"][0]["metadata"]["hnsw:space"] == "cosine"


def test_chroma_query_endpoint_lists_managed_collections(monkeypatch) -> None:
    """Expone un listado controlado de colecciones gestionadas de Chroma."""

    class FakeIndex:
        def list_collection_names(self) -> list[str]:
            return ["code_symbols", "code_files"]

    monkeypatch.setattr(server, "build_managed_vector_index", lambda: FakeIndex())
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            chroma_admin_api_enabled=True,
            chroma_admin_api_token="",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/chroma/query",
        json={"operation": "list_collections"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "list_collections"
    assert payload["result"]["collection_names"] == [
        "code_symbols",
        "code_files",
    ]


def test_chroma_query_endpoint_rejects_invalid_collection(monkeypatch) -> None:
    """Rechaza colecciones no gestionadas antes de ejecutar la operación."""

    class FakeIndex:
        def list_collection_names(self) -> list[str]:
            return ["code_symbols"]

    monkeypatch.setattr(server, "build_managed_vector_index", lambda: FakeIndex())
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            chroma_admin_api_enabled=True,
            chroma_admin_api_token="",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/chroma/query",
        json={
            "operation": "collection_count",
            "collection_name": "unknown_collection",
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["code"] == "invalid_chroma_collection"


def test_chroma_query_endpoint_executes_get(monkeypatch) -> None:
    """Delega get directo a la capa vectorial gestionada."""

    class FakeIndex:
        def list_collection_names(self) -> list[str]:
            return ["code_symbols"]

        def get_collection(
            self,
            collection_name: str,
            *,
            where: dict | None = None,
            where_document: dict | None = None,
            include: list[str] | None = None,
            limit: int | None = None,
            offset: int | None = None,
        ) -> dict[str, object]:
            assert collection_name == "code_symbols"
            assert where == {"repo_id": "mall"}
            assert include == ["metadatas", "documents"]
            assert limit == 5
            assert offset == 0
            assert where_document is None
            return {"ids": ["id-1"], "documents": ["hello"]}

    monkeypatch.setattr(server, "build_managed_vector_index", lambda: FakeIndex())
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            chroma_admin_api_enabled=True,
            chroma_admin_api_token="",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/chroma/query",
        json={
            "operation": "get",
            "collection_name": "code_symbols",
            "where": {"repo_id": "mall"},
            "include": ["metadatas", "documents"],
            "limit": 5,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["collection_name"] == "code_symbols"
    assert payload["effective_params"]["where"] == {"repo_id": "mall"}
    assert payload["result"]["ids"] == ["id-1"]


def test_chroma_query_endpoint_requires_query_texts_for_query_operation() -> None:
    """Valida el contrato del payload antes de ejecutar query en Chroma."""

    server.get_settings.cache_clear()
    
    client = TestClient(app)
    response = client.post(
        "/admin/chroma/query",
        json={
            "operation": "query",
            "collection_name": "code_symbols",
        },
    )

    assert response.status_code == 422


def test_chroma_admin_endpoints_are_disabled_by_feature_flag(monkeypatch) -> None:
    """Oculta endpoints administrativos de Chroma cuando el flag está apagado."""

    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            chroma_admin_api_enabled=False,
            chroma_admin_api_token="",
        ),
    )

    client = TestClient(app)
    response = client.get("/admin/chroma/diagnostics")

    assert response.status_code == 404


def test_chroma_query_endpoint_requires_admin_token_when_configured(
    monkeypatch,
) -> None:
    """Requiere header administrativo cuando hay token configurado."""

    class FakeIndex:
        def list_collection_names(self) -> list[str]:
            return ["code_symbols"]

    monkeypatch.setattr(server, "build_managed_vector_index", lambda: FakeIndex())
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            chroma_admin_api_enabled=True,
            chroma_admin_api_token="secret-token",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/chroma/query",
        json={"operation": "list_collections"},
    )

    assert response.status_code == 403


def test_chroma_query_endpoint_supports_filtered_collection_count(
    monkeypatch,
) -> None:
    """Permite contar subconjuntos por where sin limitarse a repo_id."""

    class FakeIndex:
        def list_collection_names(self) -> list[str]:
            return ["code_symbols"]

        def count_collection(
            self,
            collection_name: str,
            page_size: int = 500,
            where: dict | None = None,
        ) -> int:
            assert collection_name == "code_symbols"
            assert page_size == 500
            assert where == {"language": "python"}
            return 9

    monkeypatch.setattr(server, "build_managed_vector_index", lambda: FakeIndex())
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            chroma_admin_api_enabled=True,
            chroma_admin_api_token="",
        ),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/chroma/query",
        json={
            "operation": "collection_count",
            "collection_name": "code_symbols",
            "where": {"language": "python"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["count"] == 9
    assert payload["effective_params"]["where"] == {"language": "python"}
