"""Pruebas de estados operativos del JobManager durante la ingesta."""

from coderag.core.models import JobStatus, RepoIngestRequest
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

    monkeypatch.setattr(
        pipeline_module,
        "ingest_repository",
        lambda repo_url, branch, commit, logger, **kwargs: "repo-ready",
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

    runtime = manager.get_repo_runtime("repo-ready")
    assert runtime is not None
    assert runtime["last_embedding_provider"] is None
    assert runtime["last_embedding_model"] is None
