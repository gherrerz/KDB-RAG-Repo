"""Almacén de metadatos en SQLite para repositorios y trabajos."""

import sqlite3
from pathlib import Path

from coderag.core.models import JobInfo, JobStatus


class MetadataStore:
    """Almacén simple en SQLite para estado de trabajos y repositorios."""

    def __init__(self, db_path: Path) -> None:
        """Crea el almacenamiento e inicializa el esquema si es necesario."""
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        """Abre conexión sqlite con fábrica de filas habilitada."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        """Inicializa las tablas requeridas para metadatos del repositorio."""
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
        """Inserta o reemplaza la instantánea del trabajo."""
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
        """Lee la instantánea del trabajo por identificador."""
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

    def list_repo_ids(self) -> list[str]:
        """Lista ids de repositorio conocidos desde tablas de metadatos de trabajos y repos."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT id as repo_id FROM repos
                UNION
                SELECT DISTINCT repo_id as repo_id FROM jobs
                WHERE repo_id IS NOT NULL AND repo_id <> ''
                ORDER BY repo_id ASC
                """
            ).fetchall()
        return [str(row["repo_id"]) for row in rows if row["repo_id"]]
