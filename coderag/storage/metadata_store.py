"""SQLite metadata store for repositories and jobs."""

import sqlite3
from pathlib import Path

from coderag.core.models import JobInfo, JobStatus


class MetadataStore:
    """Simple SQLite-backed store for job status and repositories."""

    def __init__(self, db_path: Path) -> None:
        """Create storage and initialize schema if needed."""
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open sqlite connection with row factory enabled."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        """Initialize required tables for repository metadata."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    logs TEXT NOT NULL,
                    repo_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS repos (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def upsert_job(self, job: JobInfo) -> None:
        """Insert or replace job snapshot."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO jobs (
                    id, status, progress, logs, repo_id, error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.status.value,
                    job.progress,
                    "\n".join(job.logs),
                    job.repo_id,
                    job.error,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )

    def get_job(self, job_id: str) -> JobInfo | None:
        """Read job snapshot by identifier."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None

        logs = row["logs"].splitlines() if row["logs"] else []
        return JobInfo(
            id=row["id"],
            status=JobStatus(row["status"]),
            progress=float(row["progress"]),
            logs=logs,
            repo_id=row["repo_id"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
