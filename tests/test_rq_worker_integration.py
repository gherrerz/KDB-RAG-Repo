"""Pruebas de integración opcionales para worker RQ con Redis real."""

from uuid import uuid4

import pytest
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, SimpleWorker
from rq.timeouts import TimerDeathPenalty

from src.coderag.core.models import JobInfo, JobStatus
from src.coderag.jobs.worker import run_ingest_job_task
from src.coderag.storage.metadata_store import MetadataStore


class _TestSimpleWorker(SimpleWorker):
    """Worker de prueba con death penalty compatible en rq 1.16."""

    death_penalty_class = TimerDeathPenalty


def _require_redis() -> Redis:
    """Devuelve conexión Redis funcional o marca el test como skipped."""
    redis_conn = Redis.from_url(
        "redis://localhost:6379/0",
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
    )
    try:
        redis_conn.ping()
    except RedisError:
        pytest.skip("Redis local no disponible para prueba de integración RQ")
    return redis_conn


def test_rq_worker_processes_ingest_job_end_to_end(monkeypatch, tmp_path) -> None:
    """Valida ejecución de tarea RQ en worker burst usando Redis real."""

    redis_conn = _require_redis()

    class _Settings:
        workspace_path = tmp_path / "workspace"
        ingestion_retry_transient_only = True

    _Settings.workspace_path.mkdir(parents=True, exist_ok=True)

    import src.coderag.jobs.worker as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())

    def _fake_execute(*, job, request, store, workspace_path):
        del request, store, workspace_path
        job.status = JobStatus.completed
        job.progress = 1.0
        job.repo_id = job.repo_id or "rq-integration-repo"
        return job

    monkeypatch.setattr(module, "_execute_ingest_job", _fake_execute)

    queue_name = f"ingestion-test-{uuid4().hex[:8]}"
    queue = Queue(name=queue_name, connection=redis_conn)

    job_id = str(uuid4())
    payload = {
        "provider": "github",
        "repo_url": "https://github.com/acme/rq-integration.git",
        "branch": "main",
    }

    enqueued = queue.enqueue(run_ingest_job_task, job_id, payload)
    worker = _TestSimpleWorker(
        [queue],
        connection=redis_conn,
    )
    worker.work(burst=True, with_scheduler=False, logging_level="WARNING")

    finished = queue.fetch_job(enqueued.id)
    assert finished is not None
    assert finished.get_status() == "finished"

    metadata_path = _Settings.workspace_path.parent / "metadata.db"
    store = MetadataStore(metadata_path)
    persisted = store.get_job(job_id)
    assert persisted is not None
    assert persisted.status == JobStatus.completed
