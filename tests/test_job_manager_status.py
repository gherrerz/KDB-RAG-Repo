"""Pruebas de estados operativos del JobManager durante la ingesta."""

import datetime
from contextlib import contextmanager, nullcontext
from uuid import uuid4

import pytest

from coderag.core.models import JobInfo, JobStatus, RepoIngestRequest
from coderag.jobs.worker import (
    IngestionConflictError,
    JobManager,
    run_ingest_job_task,
)


def test_job_manager_marks_partial_when_repo_not_query_ready(
    monkeypatch,
    patch_module_settings,
    sync_thread_class,
) -> None:
    """Marca el job como partial cuando la ingesta termina sin readiness de consulta."""

    import coderag.jobs.worker as module

    patch_module_settings(module)
    manager = JobManager()

    monkeypatch.setattr(module, "Thread", sync_thread_class)

    import coderag.ingestion.pipeline as pipeline_module
    import coderag.core.storage_health as health_module

    monkeypatch.setattr(
        pipeline_module,
        "ingest_repository",
        lambda repo_url, branch, commit, logger, **kwargs: "repo-demo",
    )
    monkeypatch.setattr(
        health_module,
        "get_repo_query_status",
        lambda repo_id, listed_in_catalog: {
            "repo_id": repo_id,
            "listed_in_catalog": listed_in_catalog,
            "query_ready": False,
            "warnings": ["BM25 no cargado"],
        },
    )

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/demo.git",
        branch="main",
        commit=None,
    )
    created = manager.create_ingest_job(request)
    job = manager.get_job(created.id)
    assert job is not None
    assert job.status == JobStatus.partial
    assert any("readiness" in line.lower() for line in job.logs)


def test_job_manager_marks_completed_when_repo_query_ready(
    monkeypatch,
    patch_module_settings,
    sync_thread_class,
) -> None:
    """Marca el job como completed cuando readiness de consulta es verdadero."""

    import coderag.jobs.worker as module

    patch_module_settings(module)
    manager = JobManager()

    monkeypatch.setattr(module, "Thread", sync_thread_class)

    import coderag.ingestion.pipeline as pipeline_module
    import coderag.core.storage_health as health_module

    def _fake_ingest_repository(
        repo_url,
        branch,
        commit,
        logger,
        **kwargs,
    ) -> str:
        diagnostics_sink = kwargs.get("diagnostics_sink")
        if isinstance(diagnostics_sink, dict):
            diagnostics_sink["semantic_graph"] = {
                "enabled": True,
                "status": "ok",
                "relation_counts": 5,
                "relation_counts_by_type": {"CALLS": 3, "IMPORTS": 2},
                "java_cross_file_resolved_count": 1,
                "java_cross_file_resolved_by_type": {"CALLS": 1},
                "java_resolution_source_counts": {"import": 1, "same_package": 1},
                "unresolved_count": 1,
                "unresolved_by_type": {"IMPORTS": 1},
                "unresolved_ratio": 0.2,
                "semantic_extraction_ms": 12.5,
            }
        return "repo-ready"

    monkeypatch.setattr(
        pipeline_module,
        "ingest_repository",
        _fake_ingest_repository,
    )
    monkeypatch.setattr(
        health_module,
        "get_repo_query_status",
        lambda repo_id, listed_in_catalog: {
            "repo_id": repo_id,
            "listed_in_catalog": listed_in_catalog,
            "query_ready": True,
            "warnings": [],
        },
    )

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/ready.git",
        branch="main",
        commit=None,
    )
    created = manager.create_ingest_job(request)
    job = manager.get_job(created.id)
    assert job is not None
    assert job.status == JobStatus.completed
    assert job.diagnostics["semantic_graph"]["enabled"] is True
    assert job.diagnostics["semantic_graph"]["relation_counts"] == 5
    assert job.diagnostics["semantic_graph"]["relation_counts_by_type"] == {
        "CALLS": 3,
        "IMPORTS": 2,
    }
    assert job.diagnostics["semantic_graph"]["java_cross_file_resolved_count"] == 1
    assert job.diagnostics["semantic_graph"]["java_cross_file_resolved_by_type"] == {
        "CALLS": 1
    }
    assert job.diagnostics["semantic_graph"]["java_resolution_source_counts"] == {
        "import": 1,
        "same_package": 1,
    }
    assert job.diagnostics["semantic_graph"]["unresolved_count"] == 1
    assert job.diagnostics["semantic_graph"]["unresolved_by_type"] == {
        "IMPORTS": 1
    }

    runtime = manager.get_repo_runtime("repo-ready")
    assert runtime is not None
    assert runtime["last_embedding_provider"] == "vertex"
    assert runtime["last_embedding_model"] == "text-embedding-005"

    catalog = manager.list_repo_catalog()
    assert catalog == [
        {
            "repo_id": "repo-ready",
            "organization": "acme",
            "url": "https://github.com/acme/ready.git",
            "branch": "main",
        }
    ]


def test_job_manager_marks_transient_pipeline_failures_as_retryable(
    monkeypatch,
    patch_module_settings,
    sync_thread_class,
) -> None:
    """Marca como reintentable un fallo transitorio originado en pipeline."""

    import coderag.jobs.worker as module

    patch_module_settings(module)
    manager = JobManager()

    monkeypatch.setattr(module, "Thread", sync_thread_class)

    import coderag.ingestion.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "ingest_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("connection refused while cloning repo")
        ),
    )

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/transient.git",
        branch="main",
        commit=None,
    )
    created = manager.create_ingest_job(request)
    job = manager.get_job(created.id)

    assert job is not None
    assert job.status == JobStatus.failed
    assert job.diagnostics["retryable_error"] is True
    assert job.diagnostics["error_type"] == "RuntimeError"
    assert "connection refused" in (job.error or "")


def test_job_manager_marks_permanent_pipeline_failures_as_non_retryable(
    monkeypatch,
    patch_module_settings,
    sync_thread_class,
) -> None:
    """No marca como reintentable un fallo permanente originado en pipeline."""

    import coderag.jobs.worker as module

    patch_module_settings(module)
    manager = JobManager()

    monkeypatch.setattr(module, "Thread", sync_thread_class)

    import coderag.ingestion.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "ingest_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("repository not found")
        ),
    )

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/missing.git",
        branch="main",
        commit=None,
    )
    created = manager.create_ingest_job(request)
    job = manager.get_job(created.id)

    assert job is not None
    assert job.status == JobStatus.failed
    assert job.diagnostics["retryable_error"] is False
    assert job.diagnostics["error_type"] == "RuntimeError"
    assert job.error == "repository not found"


def test_job_manager_cleans_workspace_after_ingest_when_configured(
    monkeypatch,
    patch_module_settings,
    sync_thread_class,
) -> None:
    """Elimina el clone local al terminar la ingesta cuando la configuración lo pide."""

    import coderag.jobs.worker as module

    settings = patch_module_settings(
        module,
        retain_workspace_after_ingest=False,
    )
    manager = JobManager()

    monkeypatch.setattr(module, "Thread", sync_thread_class)

    import coderag.ingestion.pipeline as pipeline_module
    import coderag.core.storage_health as health_module

    def _fake_ingest_repository(repo_url, branch, commit, logger, **kwargs) -> str:
        del repo_url, branch, commit, logger, kwargs
        repo_path = settings.workspace_path / "repo-ready"
        repo_path.mkdir(parents=True, exist_ok=True)
        (repo_path / "README.md").write_text("demo\n", encoding="utf-8")
        return "repo-ready"

    monkeypatch.setattr(
        pipeline_module,
        "ingest_repository",
        _fake_ingest_repository,
    )

    def _fake_get_repo_query_status(repo_id, listed_in_catalog):
        assert repo_id == "repo-ready"
        assert listed_in_catalog is True
        assert not (settings.workspace_path / repo_id).exists()
        return {
            "repo_id": repo_id,
            "listed_in_catalog": listed_in_catalog,
            "workspace_available": False,
            "query_ready": True,
            "warnings": [],
        }

    monkeypatch.setattr(
        health_module,
        "get_repo_query_status",
        _fake_get_repo_query_status,
    )

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/ready.git",
        branch="main",
        commit=None,
    )
    created = manager.create_ingest_job(request)
    job = manager.get_job(created.id)

    assert job is not None
    assert job.status == JobStatus.completed
    assert not (settings.workspace_path / "repo-ready").exists()
    assert job.diagnostics["workspace_retained"] is False
    assert job.diagnostics["workspace_cleanup_attempted"] is True
    assert job.diagnostics["workspace_cleanup_succeeded"] is True
    assert any("workspace local eliminado" in line.lower() for line in job.logs)


def test_job_manager_forwards_provider_and_token_to_pipeline(
    monkeypatch,
    patch_module_settings,
    sync_thread_class,
) -> None:
    """Propaga provider/token para que pipeline decida estrategia de autenticación."""

    import coderag.jobs.worker as module

    patch_module_settings(module)
    manager = JobManager()

    monkeypatch.setattr(module, "Thread", sync_thread_class)

    import coderag.ingestion.pipeline as pipeline_module
    import coderag.core.storage_health as health_module

    captured: dict[str, object] = {}

    def _fake_ingest_repository(repo_url, branch, commit, logger, **kwargs) -> str:
        del logger
        captured["repo_url"] = repo_url
        captured["branch"] = branch
        captured["commit"] = commit
        captured["kwargs"] = dict(kwargs)
        return "repo-private"

    monkeypatch.setattr(
        pipeline_module,
        "ingest_repository",
        _fake_ingest_repository,
    )
    monkeypatch.setattr(
        health_module,
        "get_repo_query_status",
        lambda repo_id, listed_in_catalog: {
            "repo_id": repo_id,
            "listed_in_catalog": listed_in_catalog,
            "query_ready": True,
            "warnings": [],
        },
    )

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/private.git",
        branch="master",
        commit=None,
        token="ghp_test_token",
    )
    manager.create_ingest_job(request)

    assert captured["kwargs"]["provider"] == "github"
    assert captured["kwargs"]["token"] == "ghp_test_token"


def test_job_manager_recovers_interrupted_running_jobs(
    monkeypatch,
    patch_module_settings,
) -> None:
    """Convierte jobs running heredados en failed al reiniciar API."""

    import coderag.jobs.worker as module

    patch_module_settings(module)

    first_manager = JobManager()
    orphan = JobInfo(
        id=str(uuid4()),
        status=JobStatus.running,
        progress=0.5,
        logs=["Extrayendo símbolos..."],
        repo_id="orphan-repo",
        error=None,
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    first_manager.store.upsert_job(orphan)

    restarted_manager = JobManager()
    recovered = restarted_manager.get_job(orphan.id)

    assert recovered is not None
    assert recovered.status == JobStatus.failed
    assert recovered.error is not None
    assert "interrumpido" in recovered.error.lower()


def test_job_manager_lists_repos_from_metadata_without_workspace_clone(
    monkeypatch,
    patch_module_settings,
) -> None:
    """Mantiene el repo visible en catálogo aunque el workspace ya no exista."""

    import coderag.jobs.worker as module

    settings = patch_module_settings(module)
    manager = JobManager()

    manager.store.upsert_repo_runtime(
        repo_id="repo-ready",
        organization="acme",
        repo_url="https://github.com/acme/ready.git",
        branch="main",
        local_path=str(settings.workspace_path / "repo-ready"),
        embedding_provider="vertex",
        embedding_model="text-embedding-005",
    )

    assert manager.list_repo_ids() == ["repo-ready"]
    assert manager.list_repo_catalog() == [
        {
            "repo_id": "repo-ready",
            "organization": "acme",
            "url": "https://github.com/acme/ready.git",
            "branch": "main",
        }
    ]


def test_job_manager_enqueues_job_when_rq_mode_enabled(
    monkeypatch,
    patch_module_settings,
) -> None:
    """Encola jobs en backend RQ cuando el modo de ejecución lo requiere."""

    import coderag.jobs.worker as module

    patch_module_settings(
        module,
        ingestion_execution_mode="rq",
        redis_url="redis://localhost:6379/0",
    )
    manager = JobManager()

    called: dict[str, str] = {}

    def _fake_enqueue(*, job, request) -> None:
        called["job_id"] = job.id
        called["repo_url"] = request.repo_url

    monkeypatch.setattr(manager, "_repo_enqueue_lock", lambda repo_id: nullcontext())
    monkeypatch.setattr(manager, "_enqueue_ingest_job", _fake_enqueue)

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/rq-demo.git",
        branch="main",
        commit=None,
    )
    created = manager.create_ingest_job(request)

    assert created.status == JobStatus.queued
    assert called["job_id"] == created.id
    assert called["repo_url"] == request.repo_url


def test_job_manager_get_job_prefers_store_in_rq_mode(
    monkeypatch,
    patch_module_settings,
) -> None:
    """En modo RQ, get_job debe reflejar estado persistido y no caché obsoleta."""

    import coderag.jobs.worker as module

    patch_module_settings(
        module,
        ingestion_execution_mode="rq",
        redis_url="redis://localhost:6379/0",
    )
    manager = JobManager()

    job = JobInfo(id=str(uuid4()), status=JobStatus.queued)
    manager._jobs[job.id] = job

    job.status = JobStatus.running
    manager.store.upsert_job(job)

    retrieved = manager.get_job(job.id)
    assert retrieved is not None
    assert retrieved.status == JobStatus.running


def test_job_manager_rejects_duplicate_active_repo_ingest(
    monkeypatch,
    patch_module_settings,
) -> None:
    """Evita crear una segunda ingesta activa para el mismo repo_id."""

    import coderag.jobs.worker as module

    patch_module_settings(
        module,
        ingestion_execution_mode="rq",
        redis_url="redis://localhost:6379/0",
    )
    manager = JobManager()

    first = JobInfo(
        id=str(uuid4()),
        status=JobStatus.running,
        repo_id="acme-dup-repo-main",
    )
    manager.store.upsert_job(first)

    monkeypatch.setattr(manager, "_repo_enqueue_lock", lambda repo_id: nullcontext())

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/dup-repo.git",
        branch="main",
        commit=None,
    )

    with pytest.raises(IngestionConflictError):
        manager.create_ingest_job(request)


def test_job_manager_uses_repo_lock_in_rq_mode(
    monkeypatch,
    patch_module_settings,
) -> None:
    """En modo RQ debe envolver creación en lock distribuido por repo."""

    import coderag.jobs.worker as module

    patch_module_settings(
        module,
        ingestion_execution_mode="rq",
        redis_url="redis://localhost:6379/0",
    )
    manager = JobManager()

    captured: dict[str, str] = {}

    @contextmanager
    def _fake_lock(repo_id: str):
        captured["repo_id"] = repo_id
        yield

    monkeypatch.setattr(manager, "_repo_enqueue_lock", _fake_lock)
    monkeypatch.setattr(manager, "_enqueue_ingest_job", lambda **kwargs: None)

    request = RepoIngestRequest(
        provider="github",
        repo_url="https://github.com/acme/locked-repo.git",
        branch="main",
        commit=None,
    )
    created = manager.create_ingest_job(request)

    assert created.repo_id == "acme-locked-repo-main"
    assert captured["repo_id"] == "acme-locked-repo-main"


def test_run_ingest_job_task_raises_when_final_status_is_failed(
    monkeypatch,
    patch_module_settings,
) -> None:
    """Debe propagar error para que RQ aplique política de reintentos."""

    import coderag.jobs.worker as module

    patch_module_settings(module, ingestion_retry_transient_only=True)

    def _fake_execute(*, job, request, store, workspace_path):
        del request, store, workspace_path
        job.status = JobStatus.failed
        job.error = "fallo transitorio"
        job.diagnostics["retryable_error"] = True
        return job

    monkeypatch.setattr(module, "_execute_ingest_job", _fake_execute)

    payload = {
        "provider": "github",
        "repo_url": "https://github.com/acme/retry-demo.git",
        "branch": "main",
    }

    with pytest.raises(RuntimeError):
        run_ingest_job_task(str(uuid4()), payload)


def test_run_ingest_job_task_does_not_raise_for_non_retryable_failure(
    monkeypatch,
    patch_module_settings,
) -> None:
    """Con retry transitorio, no relanza errores permanentes."""

    import coderag.jobs.worker as module

    patch_module_settings(module, ingestion_retry_transient_only=True)

    def _fake_execute(*, job, request, store, workspace_path):
        del request, store, workspace_path
        job.status = JobStatus.failed
        job.error = "repository not found"
        job.diagnostics["retryable_error"] = False
        return job

    monkeypatch.setattr(module, "_execute_ingest_job", _fake_execute)

    payload = {
        "provider": "github",
        "repo_url": "https://github.com/acme/missing.git",
        "branch": "main",
    }

    assert run_ingest_job_task(str(uuid4()), payload) == ""


def test_run_ingest_job_task_raises_when_retry_all_enabled(
    monkeypatch,
    patch_module_settings,
) -> None:
    """Con retry-all habilitado, cualquier fallo debe relanzarse."""

    import coderag.jobs.worker as module

    patch_module_settings(module, ingestion_retry_transient_only=False)

    def _fake_execute(*, job, request, store, workspace_path):
        del request, store, workspace_path
        job.status = JobStatus.failed
        job.error = "repository not found"
        job.diagnostics["retryable_error"] = False
        return job

    monkeypatch.setattr(module, "_execute_ingest_job", _fake_execute)

    payload = {
        "provider": "github",
        "repo_url": "https://github.com/acme/missing.git",
        "branch": "main",
    }

    with pytest.raises(RuntimeError):
        run_ingest_job_task(str(uuid4()), payload)
