"""Pruebas de estados operativos del JobManager durante la ingesta."""

import datetime
from uuid import uuid4

from coderag.core.models import JobInfo, JobStatus, RepoIngestRequest
from coderag.jobs.worker import JobManager


def test_job_manager_marks_partial_when_repo_not_query_ready(
    monkeypatch,
    tmp_path,
) -> None:
    """Marca el job como partial cuando la ingesta termina sin readiness de consulta."""

    class _Settings:
        workspace_path = tmp_path / "workspace"

    _Settings.workspace_path.mkdir(parents=True, exist_ok=True)

    import coderag.jobs.worker as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    manager = JobManager()

    class _SyncThread:
        def __init__(self, target, args, daemon):
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(module, "Thread", _SyncThread)

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
        token=None,
        commit=None,
    )
    created = manager.create_ingest_job(request)
    job = manager.get_job(created.id)
    assert job is not None
    assert job.status == JobStatus.partial
    assert any("readiness" in line.lower() for line in job.logs)


def test_job_manager_marks_completed_when_repo_query_ready(
    monkeypatch,
    tmp_path,
) -> None:
    """Marca el job como completed cuando readiness de consulta es verdadero."""

    class _Settings:
        workspace_path = tmp_path / "workspace"

    _Settings.workspace_path.mkdir(parents=True, exist_ok=True)

    import coderag.jobs.worker as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    manager = JobManager()

    class _SyncThread:
        def __init__(self, target, args, daemon):
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(module, "Thread", _SyncThread)

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
        token=None,
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
    assert runtime["last_embedding_provider"] is None
    assert runtime["last_embedding_model"] is None


def test_job_manager_recovers_interrupted_running_jobs(
    monkeypatch,
    tmp_path,
) -> None:
    """Convierte jobs running heredados en failed al reiniciar API."""

    class _Settings:
        workspace_path = tmp_path / "workspace"

    _Settings.workspace_path.mkdir(parents=True, exist_ok=True)

    import coderag.jobs.worker as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())

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
