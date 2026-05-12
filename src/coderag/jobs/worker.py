"""Gestión de trabajos para ingesta con backend thread o Redis/RQ."""

import datetime
from contextlib import contextmanager
import inspect
import os
from pathlib import Path
import shutil
import stat
from threading import Lock, Thread
import time
from uuid import uuid4

from coderag.core.models import JobInfo, JobStatus, RepoIngestRequest
from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.ingestion.git_client import build_repo_id, extract_repo_organization
from coderag.storage.metadata_store import MetadataStore
from coderag.storage.base_metadata_store import BaseMetadataStore


class IngestionConflictError(RuntimeError):
    """Error de conflicto cuando ya existe ingesta activa para el repositorio."""


def _is_non_retryable_ingest_error(message: str) -> bool:
    """Detecta errores permanentes donde reintentar no agrega valor."""
    normalized = (message or "").lower()
    non_retryable_markers = (
        "authentication failed",
        "permission denied",
        "not found",
        "repository not found",
        "no se pudo clonar",
        "commit solicitado no está disponible",
        "invalid",
        "forbidden",
        "unauthorized",
    )
    return any(marker in normalized for marker in non_retryable_markers)


def _is_transient_ingest_error(message: str) -> bool:
    """Detecta errores transitorios típicos de red/infraestructura/locks."""
    normalized = (message or "").lower()
    transient_markers = (
        "timeout",
        "timed out",
        "temporarily unavailable",
        "temporary failure",
        "connection refused",
        "connection reset",
        "service unavailable",
        "name resolution",
        "too many requests",
        "rate limit",
        "429",
        "deadlock",
        "database is locked",
        "connection aborted",
    )
    return any(marker in normalized for marker in transient_markers)


def _is_retryable_ingest_error(message: str) -> bool:
    """Clasifica si un error de ingesta amerita reintento automático."""
    if _is_non_retryable_ingest_error(message):
        return False
    return _is_transient_ingest_error(message)


def _on_remove_error(func, path: str, exc_info) -> None:
    """Permite borrar archivos readonly durante cleanup del workspace en Windows."""
    del exc_info
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_workspace_clone(path: Path, retries: int = 3) -> bool:
    """Elimina el clone local con reintentos, sin volver la ingesta fallida."""
    if not path.exists():
        return True

    for _ in range(retries):
        try:
            shutil.rmtree(path, onerror=_on_remove_error)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(0.4)
    return False


def _invoke_execute_ingest_job(
    *,
    job: JobInfo,
    request: RepoIngestRequest,
    store: MetadataStore,
    workspace_path: Path,
    retain_workspace_after_ingest: bool,
) -> JobInfo:
    """Invoca `_execute_ingest_job` manteniendo compatibilidad con tests legacy."""
    execute_params = inspect.signature(_execute_ingest_job).parameters
    kwargs = {
        "job": job,
        "request": request,
        "store": store,
        "workspace_path": workspace_path,
    }
    if "retain_workspace_after_ingest" in execute_params:
        kwargs["retain_workspace_after_ingest"] = retain_workspace_after_ingest
    return _execute_ingest_job(**kwargs)


def _execute_ingest_job(
    *,
    job: JobInfo,
    request: RepoIngestRequest,
    store: MetadataStore,
    workspace_path: Path,
    retain_workspace_after_ingest: bool,
) -> JobInfo:
    """Ejecuta una ingesta y sincroniza estado/logs en storage persistente."""
    job.status = JobStatus.running
    job.updated_at = datetime.datetime.now(datetime.UTC)
    store.upsert_job(job)

    def logger(message: str) -> None:
        job.logs.append(message)
        steps = max(1, len(job.logs))
        job.progress = min(0.95, steps / 8)
        job.updated_at = datetime.datetime.now(datetime.UTC)
        store.upsert_job(job)

    try:
        from coderag.core.storage_health import get_repo_query_status
        from coderag.ingestion.pipeline import ingest_repository

        ingest_diagnostics: dict[str, object] = {}

        repo_id = ingest_repository(
            provider=request.provider,
            repo_url=request.repo_url,
            branch=request.branch,
            commit=request.commit,
            token=request.token,
            auth=request.resolved_auth(),
            embedding_provider=request.embedding_provider,
            embedding_model=request.embedding_model,
            logger=logger,
            diagnostics_sink=ingest_diagnostics,
        )
        job.repo_id = repo_id
        job.diagnostics = ingest_diagnostics
        organization = extract_repo_organization(request.repo_url)
        store.upsert_repo_runtime(
            repo_id=repo_id,
            organization=organization,
            repo_url=request.repo_url,
            branch=request.branch,
            local_path=str(workspace_path / repo_id),
            embedding_provider=request.embedding_provider,
            embedding_model=request.embedding_model,
        )
        workspace_clone_path = workspace_path / repo_id
        job.diagnostics["workspace_retained"] = retain_workspace_after_ingest
        if not retain_workspace_after_ingest:
            cleaned = _remove_workspace_clone(workspace_clone_path)
            job.diagnostics["workspace_cleanup_attempted"] = True
            job.diagnostics["workspace_cleanup_succeeded"] = cleaned
            if cleaned:
                job.logs.append(
                    "Workspace local eliminado tras la ingesta por configuración."
                )
            else:
                job.logs.append(
                    "Advertencia: no se pudo eliminar el workspace local tras la ingesta."
                )
        job.progress = 1.0
        readiness = get_repo_query_status(
            repo_id=repo_id,
            listed_in_catalog=True,
        )
        if readiness.get("query_ready"):
            job.status = JobStatus.completed
        else:
            job.status = JobStatus.partial
            job.logs.append(
                "Ingesta finalizada parcialmente: el repositorio aún no está "
                "listo para consultas."
            )
            for warning in readiness.get("warnings") or []:
                job.logs.append(f"Advertencia readiness: {warning}")
    except Exception as exc:
        job.status = JobStatus.failed
        job.error = str(exc)
        job.diagnostics["retryable_error"] = _is_retryable_ingest_error(job.error)
        job.diagnostics["error_type"] = exc.__class__.__name__
        job.logs.append(f"Error: {exc}")
    finally:
        job.updated_at = datetime.datetime.now(datetime.UTC)
        store.upsert_job(job)

    return job


def _build_metadata_store() -> BaseMetadataStore:
    """Crea el store de metadatos apropiado según la configuración activa."""
    settings = get_settings()
    postgres_dsn = resolve_postgres_dsn(settings)
    if postgres_dsn:
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore
        return PostgresMetadataStore(postgres_dsn)
    metadata_path = settings.workspace_path.parent / "metadata.db"
    return MetadataStore(metadata_path)


def run_ingest_job_task(job_id: str, request_payload: dict[str, object]) -> str:
    """Tarea RQ: ejecuta ingesta de forma desacoplada del proceso API."""
    settings = get_settings()
    store = _build_metadata_store()

    request = RepoIngestRequest.model_validate(request_payload)
    job = store.get_job(job_id)
    if job is None:
        job = JobInfo(id=job_id, status=JobStatus.queued)
        store.upsert_job(job)

    final_job = _invoke_execute_ingest_job(
        job=job,
        request=request,
        store=store,
        workspace_path=settings.workspace_path,
        retain_workspace_after_ingest=getattr(
            settings,
            "retain_workspace_after_ingest",
            True,
        ),
    )

    # In RQ mode, failures must raise to trigger retry policy.
    if final_job.status == JobStatus.failed:
        retryable_error = bool(final_job.diagnostics.get("retryable_error", False))
        retry_transient_only = bool(
            getattr(settings, "ingestion_retry_transient_only", True)
        )

        should_retry = retryable_error or not retry_transient_only
        if should_retry:
            raise RuntimeError(final_job.error or "Ingesta falló en worker RQ")

    return final_job.repo_id or ""


class JobManager:
    """Realiza un seguimiento de los trabajos de ingesta y los ejecuta en subprocesos en segundo plano."""

    def __init__(self) -> None:
        """Inicialice el administrador con almacenamiento de metadatos."""
        settings = get_settings()
        self._metadata_path = settings.workspace_path.parent / "metadata.db"
        self._workspace_path = settings.workspace_path
        self._retain_workspace_after_ingest = getattr(
            settings,
            "retain_workspace_after_ingest",
            True,
        )
        self._ingestion_mode = getattr(settings, "ingestion_execution_mode", "thread")
        self.store = _build_metadata_store()
        self.store.recover_interrupted_jobs()
        self._jobs: dict[str, JobInfo] = {}
        self._create_job_lock = Lock()

    def list_repo_ids(self) -> list[str]:
        """Devuelve identificadores de repositorio conocidos desde metadatos persistidos."""
        return self.store.list_repo_ids()

    def list_repo_catalog(self) -> list[dict[str, str | None]]:
        """Devuelve catálogo de repos conocidos con metadata de ingesta persistida."""
        return self.store.list_repo_catalog()

    def get_repo_runtime(self, repo_id: str) -> dict[str, str | None] | None:
        """Devuelve metadata runtime de la última ingesta del repositorio."""
        return self.store.get_repo_runtime(repo_id)

    def reset_all_data(self) -> tuple[list[str], list[str]]:
        """Restablezca todos los índices persistentes y el estado del trabajo/caché en memoria."""
        running_jobs = self.store.list_active_job_ids()
        if running_jobs:
            joined = ", ".join(running_jobs)
            raise RuntimeError(
                "No se puede limpiar mientras haya ingestas en ejecución: "
                f"{joined}"
            )

        from coderag.maintenance.reset_service import reset_all_storage

        cleared, warnings = reset_all_storage()
        self._jobs.clear()
        self.store = _build_metadata_store()
        return cleared, warnings

    def delete_repo(self, repo_id: str) -> tuple[list[str], list[str], dict[str, int]]:
        """Elimina un repositorio por ID de todas las capas de storage."""
        normalized_repo_id = repo_id.strip()
        if not normalized_repo_id:
            raise ValueError("repo_id no puede estar vacío")

        running_same_repo_jobs = self.store.list_active_job_ids(
            repo_id=normalized_repo_id,
        )
        if running_same_repo_jobs:
            joined = ", ".join(running_same_repo_jobs)
            raise RuntimeError(
                "No se puede eliminar el repositorio mientras haya "
                f"ingestas activas del mismo repo: {joined}"
            )

        if normalized_repo_id not in self.list_repo_ids():
            raise LookupError(
                f"No existe un repositorio registrado con id '{normalized_repo_id}'"
            )

        from coderag.maintenance.reset_service import delete_repo_storage

        cleared, warnings, deleted_counts = delete_repo_storage(normalized_repo_id)

        tracked_jobs = [
            job_id
            for job_id, job in self._jobs.items()
            if (job.repo_id or "").strip() == normalized_repo_id
        ]
        for job_id in tracked_jobs:
            self._jobs.pop(job_id, None)

        return cleared, warnings, deleted_counts

    def create_ingest_job(self, request: RepoIngestRequest) -> JobInfo:
        """Cree e inicie un trabajo de ingesta asincrónica."""
        repo_id = build_repo_id(request.repo_url, request.branch)

        with self._create_job_lock:
            if self._ingestion_mode == "rq":
                with self._repo_enqueue_lock(repo_id=repo_id):
                    return self._create_ingest_job_unlocked(
                        request=request,
                        repo_id=repo_id,
                    )

            return self._create_ingest_job_unlocked(
                request=request,
                repo_id=repo_id,
            )

    def _create_ingest_job_unlocked(
        self,
        *,
        request: RepoIngestRequest,
        repo_id: str,
    ) -> JobInfo:
        """Crea y dispara job luego de validar conflictos activos por repo_id."""
        active_jobs = self.store.list_active_job_ids(repo_id=repo_id)
        if active_jobs:
            joined = ", ".join(active_jobs)
            raise IngestionConflictError(
                "Ya existe una ingesta activa para el repositorio "
                f"'{repo_id}': {joined}"
            )

        job_id = str(uuid4())
        job = JobInfo(id=job_id, status=JobStatus.queued, repo_id=repo_id)
        self._jobs[job_id] = job
        self.store.upsert_job(job)

        if self._ingestion_mode == "rq":
            try:
                self._enqueue_ingest_job(job=job, request=request)
            except Exception as exc:
                job.status = JobStatus.failed
                job.error = f"No se pudo encolar job en Redis/RQ: {exc}"
                job.logs.append(job.error)
                job.updated_at = datetime.datetime.now(datetime.UTC)
                self.store.upsert_job(job)
                raise RuntimeError(job.error) from exc
        else:
            thread = Thread(
                target=self._run_ingest_job,
                args=(job_id, request),
                daemon=True,
            )
            thread.start()
        return job

    @contextmanager
    def _repo_enqueue_lock(self, repo_id: str):
        """Serializa creación/enqueue por repo_id usando lock Redis en modo RQ."""
        from redis import Redis

        settings = get_settings()
        lock_timeout = int(getattr(settings, "ingestion_enqueue_lock_seconds", 30))
        blocking_timeout = int(
            getattr(settings, "ingestion_enqueue_lock_wait_seconds", 5)
        )
        redis_conn = Redis.from_url(settings.redis_url)
        lock_key = f"coderag:ingest:enqueue:{repo_id}"
        lock = redis_conn.lock(
            lock_key,
            timeout=lock_timeout,
            blocking_timeout=blocking_timeout,
        )

        acquired = lock.acquire(blocking=True)
        if not acquired:
            raise IngestionConflictError(
                "No se pudo adquirir lock de ingesta para el repositorio "
                f"'{repo_id}'. Intenta nuevamente."
            )

        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:
                pass

    def _enqueue_ingest_job(self, *, job: JobInfo, request: RepoIngestRequest) -> None:
        """Encola trabajo de ingesta en Redis/RQ para ejecución distribuida."""
        from redis import Redis
        from rq import Queue, Retry

        settings = get_settings()
        queue_name = getattr(settings, "ingestion_queue_name", "ingestion")
        job_timeout = int(getattr(settings, "ingestion_job_timeout_seconds", 7200))
        result_ttl = int(getattr(settings, "ingestion_result_ttl_seconds", 86400))
        failure_ttl = int(getattr(settings, "ingestion_failure_ttl_seconds", 604800))
        retry_max = int(getattr(settings, "ingestion_retry_max", 3))
        retry_intervals = settings.resolve_ingestion_retry_intervals()

        redis_conn = Redis.from_url(settings.redis_url)
        queue = Queue(name=queue_name, connection=redis_conn)
        retry = None
        if retry_max > 0:
            retry = Retry(max=retry_max, interval=retry_intervals or None)

        rq_job = queue.enqueue(
            run_ingest_job_task,
            job.id,
            request.model_dump(mode="python"),
            job_timeout=job_timeout,
            result_ttl=result_ttl,
            failure_ttl=failure_ttl,
            retry=retry,
        )
        job.logs.append(
            f"Job encolado en Redis/RQ ({queue_name}) con id {rq_job.id}."
        )
        job.updated_at = datetime.datetime.now(datetime.UTC)
        self.store.upsert_job(job)

    def get_job(self, job_id: str) -> JobInfo | None:
        """Obtenga el estado del trabajo desde la memoria o el almacenamiento persistente."""
        if self._ingestion_mode == "rq":
            stored_job = self.store.get_job(job_id)
            if stored_job is not None:
                self._jobs[job_id] = stored_job
            return stored_job

        job = self._jobs.get(job_id)
        if job is not None:
            return job
        return self.store.get_job(job_id)

    def _run_ingest_job(self, job_id: str, request: RepoIngestRequest) -> None:
        """Ejecute el flujo de trabajo de ingesta y actualice las transiciones de estado."""
        job = self._jobs[job_id]
        _invoke_execute_ingest_job(
            job=job,
            request=request,
            store=self.store,
            workspace_path=self._workspace_path,
            retain_workspace_after_ingest=self._retain_workspace_after_ingest,
        )


if __name__ == "__main__":
    print("Job worker está disponible vía JobManager embebido en API.")
