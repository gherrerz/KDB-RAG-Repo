"""Almacén de metadatos en SQLite para repositorios y trabajos."""

import sqlite3
from pathlib import Path
from datetime import datetime

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
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    embedding_provider TEXT,
                    embedding_model TEXT
                )
                """
            )
            self._ensure_repo_runtime_columns(connection)

    @staticmethod
    def _ensure_repo_runtime_columns(connection: sqlite3.Connection) -> None:
        """Garantiza columnas runtime en repos para bases existentes."""
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(repos)").fetchall()
        }
        required_columns = {
            "updated_at": "TEXT",
            "embedding_provider": "TEXT",
            "embedding_model": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name in columns:
                continue
            connection.execute(
                f"ALTER TABLE repos ADD COLUMN {column_name} {column_type}"
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

    def upsert_repo_runtime(
        self,
        *,
        repo_id: str,
        repo_url: str,
        branch: str,
        local_path: str,
        embedding_provider: str | None,
        embedding_model: str | None,
    ) -> None:
        """Inserta o actualiza metadata runtime por repositorio."""
        now = datetime.utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO repos (
                    id, url, branch, local_path, created_at,
                    updated_at, embedding_provider, embedding_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    url=excluded.url,
                    branch=excluded.branch,
                    local_path=excluded.local_path,
                    updated_at=excluded.updated_at,
                    embedding_provider=excluded.embedding_provider,
                    embedding_model=excluded.embedding_model
                """,
                (
                    repo_id,
                    repo_url,
                    branch,
                    local_path,
                    now,
                    now,
                    embedding_provider,
                    embedding_model,
                ),
            )

    def get_repo_runtime(self, repo_id: str) -> dict[str, str | None] | None:
        """Obtiene metadata runtime almacenada para un repositorio."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT embedding_provider, embedding_model
                FROM repos
                WHERE id = ?
                """,
                (repo_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "last_embedding_provider": row["embedding_provider"],
            "last_embedding_model": row["embedding_model"],
        }
