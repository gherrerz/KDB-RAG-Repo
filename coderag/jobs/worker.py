"""Gestión de trabajos para ingesta con integración opcional de Redis/RQ."""

import datetime
from threading import Thread
from uuid import uuid4

from coderag.core.models import JobInfo, JobStatus, RepoIngestRequest
from coderag.core.settings import get_settings
from coderag.storage.metadata_store import MetadataStore


class JobManager:
    """Realiza un seguimiento de los trabajos de ingesta y los ejecuta en subprocesos en segundo plano."""

    def __init__(self) -> None:
        """Inicialice el administrador con almacenamiento de metadatos."""
        settings = get_settings()
        self._metadata_path = settings.workspace_path.parent / "metadata.db"
        self._workspace_path = settings.workspace_path
        self.store = MetadataStore(self._metadata_path)
        self.store.recover_interrupted_jobs()
        self._jobs: dict[str, JobInfo] = {}

    def list_repo_ids(self) -> list[str]:
        """Devuelve identificadores de repositorio conocidos de metadatos y espacio de trabajo local."""
        repo_ids = set(self.store.list_repo_ids())
        if self._workspace_path.exists() and self._workspace_path.is_dir():
            for child in self._workspace_path.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    repo_ids.add(child.name)
        return sorted(repo_ids)

    def get_repo_runtime(self, repo_id: str) -> dict[str, str | None] | None:
        """Devuelve metadata runtime de la última ingesta del repositorio."""
        return self.store.get_repo_runtime(repo_id)

    def reset_all_data(self) -> tuple[list[str], list[str]]:
        """Restablezca todos los índices persistentes y el estado del trabajo/caché en memoria."""
        running_jobs = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status == JobStatus.running
        ]
        if running_jobs:
            joined = ", ".join(running_jobs)
            raise RuntimeError(
                "No se puede limpiar mientras haya ingestas en ejecución: "
                f"{joined}"
            )

        from coderag.maintenance.reset_service import reset_all_storage

        cleared, warnings = reset_all_storage()
        self._jobs.clear()
        self.store = MetadataStore(self._metadata_path)
        return cleared, warnings

    def delete_repo(self, repo_id: str) -> tuple[list[str], list[str], dict[str, int]]:
        """Elimina un repositorio por ID de todas las capas de storage."""
        normalized_repo_id = repo_id.strip()
        if not normalized_repo_id:
            raise ValueError("repo_id no puede estar vacío")

        running_same_repo_jobs = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status == JobStatus.running
            and (job.repo_id or "").strip() == normalized_repo_id
        ]
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
        job_id = str(uuid4())
        job = JobInfo(id=job_id, status=JobStatus.queued)
        self._jobs[job_id] = job
        self.store.upsert_job(job)

        thread = Thread(target=self._run_ingest_job, args=(job_id, request), daemon=True)
        thread.start()
        return job

    def get_job(self, job_id: str) -> JobInfo | None:
        """Obtenga el estado del trabajo desde la memoria o el almacenamiento persistente."""
        job = self._jobs.get(job_id)
        if job is not None:
            return job
        return self.store.get_job(job_id)

    def _run_ingest_job(self, job_id: str, request: RepoIngestRequest) -> None:
        """Ejecute el flujo de trabajo de ingesta y actualice las transiciones de estado."""
        job = self._jobs[job_id]
        job.status = JobStatus.running
        job.updated_at = datetime.datetime.now(datetime.UTC)
        def logger(message: str) -> None:
            job.logs.append(message)
            steps = max(1, len(job.logs))
            job.progress = min(0.95, steps / 8)
            job.updated_at = datetime.datetime.now(datetime.UTC)
            self.store.upsert_job(job)
        try:
            from coderag.ingestion.pipeline import ingest_repository
            from coderag.core.storage_health import get_repo_query_status

            ingest_diagnostics: dict[str, object] = {}

            repo_id = ingest_repository(
                repo_url=request.repo_url,
                branch=request.branch,
                commit=request.commit,
                embedding_provider=request.embedding_provider,
                embedding_model=request.embedding_model,
                logger=logger,
                diagnostics_sink=ingest_diagnostics,
            )
            job.repo_id = repo_id
            job.diagnostics = ingest_diagnostics
            self.store.upsert_repo_runtime(
                repo_id=repo_id,
                repo_url=request.repo_url,
                branch=request.branch,
                local_path=str(self._workspace_path / repo_id),
                embedding_provider=request.embedding_provider,
                embedding_model=request.embedding_model,
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
            job.logs.append(f"Error: {exc}")
        finally:
            job.updated_at = datetime.datetime.now(datetime.UTC)
            self.store.upsert_job(job)


if __name__ == "__main__":
    print("Job worker está disponible vía JobManager embebido en API.")
