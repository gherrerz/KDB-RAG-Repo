"""Pruebas del contrato base sobre la implementación SQLite de metadata."""

from __future__ import annotations

import datetime
from pathlib import Path

from coderag.core.models import JobInfo, JobStatus
from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.metadata_store import MetadataStore


def _make_store(tmp_path: Path) -> MetadataStore:
    """Crea un metadata store SQLite aislado para cada prueba."""
    return MetadataStore(tmp_path / "metadata.db")


def _make_job(
    *,
    job_id: str = "job-1",
    status: JobStatus = JobStatus.queued,
    repo_id: str | None = "repo-1",
) -> JobInfo:
    """Construye un JobInfo mínimo para pruebas de persistencia."""
    timestamp = datetime.datetime.now(datetime.UTC)
    return JobInfo(
        id=job_id,
        status=status,
        progress=0.5,
        logs=["inicio"],
        repo_id=repo_id,
        error=None,
        diagnostics={"source": "test"},
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_metadata_store_implements_base_contract(tmp_path: Path) -> None:
    """La implementación SQLite debe cumplir el contrato BaseMetadataStore."""
    store = _make_store(tmp_path)

    assert isinstance(store, BaseMetadataStore)


def test_upsert_job_round_trip_preserves_core_fields(tmp_path: Path) -> None:
    """Persistir y leer un job mantiene estado, logs y diagnostics."""
    store = _make_store(tmp_path)
    job = _make_job(status=JobStatus.running)

    store.upsert_job(job)
    loaded = store.get_job(job.id)

    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.status == JobStatus.running
    assert loaded.logs == ["inicio"]
    assert loaded.diagnostics == {"source": "test"}


def test_recover_interrupted_jobs_marks_running_jobs_failed(
    tmp_path: Path,
) -> None:
    """Los jobs running heredados deben recuperarse como failed."""
    store = _make_store(tmp_path)
    job = _make_job(status=JobStatus.running)

    store.upsert_job(job)

    recovered_count = store.recover_interrupted_jobs()
    loaded = store.get_job(job.id)

    assert recovered_count == 1
    assert loaded is not None
    assert loaded.status == JobStatus.failed
    assert loaded.error is not None
    assert "interrumpido" in loaded.error.lower()


def test_repo_runtime_round_trip_and_catalog_listing(tmp_path: Path) -> None:
    """El store debe listar repos y exponer runtime persistido."""
    store = _make_store(tmp_path)
    store.upsert_repo_runtime(
        repo_id="repo-1",
        organization="acme",
        repo_url="https://github.com/acme/demo.git",
        branch="main",
        local_path=str(tmp_path / "workspace" / "repo-1"),
        embedding_provider="vertex",
        embedding_model="text-embedding-005",
    )

    runtime = store.get_repo_runtime("repo-1")

    assert store.list_repo_ids() == ["repo-1"]
    assert store.list_repo_catalog() == [
        {
            "repo_id": "repo-1",
            "organization": "acme",
            "url": "https://github.com/acme/demo.git",
            "branch": "main",
        }
    ]
    assert runtime == {
        "last_embedding_provider": "vertex",
        "last_embedding_model": "text-embedding-005",
    }


def test_delete_repo_data_removes_jobs_and_runtime(tmp_path: Path) -> None:
    """Borrar datos de repo elimina jobs asociados y runtime persistido."""
    store = _make_store(tmp_path)
    store.upsert_repo_runtime(
        repo_id="repo-1",
        organization="acme",
        repo_url="https://github.com/acme/demo.git",
        branch="main",
        local_path=str(tmp_path / "workspace" / "repo-1"),
        embedding_provider="vertex",
        embedding_model="text-embedding-005",
    )
    store.upsert_job(_make_job(repo_id="repo-1"))

    result = store.delete_repo_data("repo-1")

    assert result == {
        "jobs_deleted": 1,
        "repos_deleted": 1,
        "total": 2,
    }
    assert store.get_repo_runtime("repo-1") is None
    assert store.list_repo_ids() == []


def test_list_active_job_ids_filters_by_repo(tmp_path: Path) -> None:
    """El filtro por repo debe devolver solo jobs activos del repositorio."""
    store = _make_store(tmp_path)
    store.upsert_job(_make_job(job_id="job-1", status=JobStatus.queued, repo_id="r1"))
    store.upsert_job(_make_job(job_id="job-2", status=JobStatus.running, repo_id="r2"))
    store.upsert_job(_make_job(job_id="job-3", status=JobStatus.completed, repo_id="r1"))

    assert store.list_active_job_ids() == ["job-1", "job-2"]
    assert store.list_active_job_ids(repo_id="r1") == ["job-1"]