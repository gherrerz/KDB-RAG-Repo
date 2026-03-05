"""Job management for ingestion with optional Redis/RQ integration."""

from datetime import datetime
from threading import Thread
from uuid import uuid4

from coderag.core.models import JobInfo, JobStatus, RepoIngestRequest
from coderag.core.settings import get_settings
from coderag.storage.metadata_store import MetadataStore


class JobManager:
    """Tracks ingestion jobs and executes them in background threads."""

    def __init__(self) -> None:
        """Initialize manager with metadata storage."""
        settings = get_settings()
        self.store = MetadataStore(settings.workspace_path.parent / "metadata.db")
        self._jobs: dict[str, JobInfo] = {}

    def create_ingest_job(self, request: RepoIngestRequest) -> JobInfo:
        """Create and start an asynchronous ingestion job."""
        job_id = str(uuid4())
        job = JobInfo(id=job_id, status=JobStatus.queued)
        self._jobs[job_id] = job
        self.store.upsert_job(job)

        thread = Thread(target=self._run_ingest_job, args=(job_id, request), daemon=True)
        thread.start()
        return job

    def get_job(self, job_id: str) -> JobInfo | None:
        """Get job state from memory or persisted storage."""
        job = self._jobs.get(job_id)
        if job is not None:
            return job
        return self.store.get_job(job_id)

    def _run_ingest_job(self, job_id: str, request: RepoIngestRequest) -> None:
        """Execute ingestion workflow and update status transitions."""
        job = self._jobs[job_id]
        job.status = JobStatus.running
        job.updated_at = datetime.utcnow()

        def logger(message: str) -> None:
            job.logs.append(message)
            steps = max(1, len(job.logs))
            job.progress = min(0.95, steps / 8)
            job.updated_at = datetime.utcnow()
            self.store.upsert_job(job)

        try:
            from coderag.ingestion.pipeline import ingest_repository

            repo_id = ingest_repository(
                repo_url=request.repo_url,
                branch=request.branch,
                commit=request.commit,
                logger=logger,
            )
            job.repo_id = repo_id
            job.progress = 1.0
            job.status = JobStatus.completed
        except Exception as exc:
            job.status = JobStatus.failed
            job.error = str(exc)
            job.logs.append(f"Error: {exc}")
        finally:
            job.updated_at = datetime.utcnow()
            self.store.upsert_job(job)


if __name__ == "__main__":
    print("Job worker está disponible vía JobManager embebido en API.")
