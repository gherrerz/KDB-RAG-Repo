"""Almacén de metadatos operativos sobre PostgreSQL."""

from __future__ import annotations

import datetime
import json
from typing import Any

import psycopg
from psycopg.rows import dict_row

from coderag.core.models import JobInfo, JobStatus
from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.postgres_table_names import (
    POSTGRES_JOBS_TABLE,
    POSTGRES_REPOS_TABLE,
)


class PostgresMetadataStore(BaseMetadataStore):
    """Implementación de BaseMetadataStore usando PostgreSQL vía psycopg3."""

    def __init__(self, postgres_dsn: str) -> None:
        """Crea la instancia y garantiza que el esquema existe."""
        self._url = postgres_dsn
        self._init_schema()

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        """Abre conexión a Postgres con row_factory dict_row."""
        return psycopg.connect(self._url, row_factory=dict_row)

    def _init_schema(self) -> None:
        """Crea las tablas requeridas si no existen."""
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {POSTGRES_JOBS_TABLE} (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    logs TEXT NOT NULL,
                    repo_id TEXT,
                    error TEXT,
                    diagnostics TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {POSTGRES_REPOS_TABLE} (
                    id TEXT PRIMARY KEY,
                    organization TEXT,
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

    def upsert_job(self, job: JobInfo) -> None:
        """Inserta o reemplaza la instantánea del trabajo."""
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {POSTGRES_JOBS_TABLE} (
                    id, status, progress, logs, repo_id, error,
                    diagnostics, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    progress = EXCLUDED.progress,
                    logs = EXCLUDED.logs,
                    repo_id = EXCLUDED.repo_id,
                    error = EXCLUDED.error,
                    diagnostics = EXCLUDED.diagnostics,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    job.id,
                    job.status.value,
                    job.progress,
                    "\n".join(job.logs),
                    job.repo_id,
                    job.error,
                    json.dumps(job.diagnostics, ensure_ascii=True),
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )

    def recover_interrupted_jobs(self) -> int:
        """Marca jobs queued/running como failed tras reinicio inesperado."""
        reason = (
            "Job interrumpido por reinicio del servicio. "
            "Reintenta la ingesta."
        )
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE {POSTGRES_JOBS_TABLE}
                SET
                    status = %s,
                    error = CASE
                        WHEN error IS NULL OR error = '' THEN %s
                        ELSE error
                    END,
                    logs = CASE
                        WHEN logs IS NULL OR logs = '' THEN %s
                        ELSE logs || chr(10) || %s
                    END,
                    updated_at = %s
                WHERE status IN (%s, %s)
                """,
                (
                    JobStatus.failed.value,
                    reason,
                    reason,
                    reason,
                    now,
                    JobStatus.queued.value,
                    JobStatus.running.value,
                ),
            )
            return int(cursor.rowcount or 0)

    def get_job(self, job_id: str) -> JobInfo | None:
        """Lee la instantánea del trabajo por identificador."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {POSTGRES_JOBS_TABLE} WHERE id = %s",
                (job_id,),
            ).fetchone()
        if row is None:
            return None

        logs = row["logs"].splitlines() if row["logs"] else []
        diagnostics: dict = {}
        if row.get("diagnostics"):
            try:
                loaded = json.loads(row["diagnostics"])
                if isinstance(loaded, dict):
                    diagnostics = loaded
            except Exception:
                diagnostics = {}

        return JobInfo(
            id=row["id"],
            status=JobStatus(row["status"]),
            progress=float(row["progress"]),
            logs=logs,
            repo_id=row["repo_id"],
            error=row["error"],
            diagnostics=diagnostics,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_repo_ids(self) -> list[str]:
        """Lista ids de repositorio conocidos desde tablas de jobs y repos."""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT id AS repo_id FROM {POSTGRES_REPOS_TABLE}
                UNION
                SELECT DISTINCT repo_id FROM {POSTGRES_JOBS_TABLE}
                WHERE repo_id IS NOT NULL AND repo_id <> ''
                ORDER BY repo_id ASC
                """
            ).fetchall()
        return [str(row["repo_id"]) for row in rows if row["repo_id"]]

    def list_repo_catalog(self) -> list[dict[str, str | None]]:
        """Retorna catálogo de repos persistidos con metadata de ingesta."""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id AS repo_id, organization, url, branch
                FROM {POSTGRES_REPOS_TABLE}
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            {
                "repo_id": str(row["repo_id"]),
                "organization": (
                    str(row["organization"])
                    if row["organization"] is not None
                    else None
                ),
                "url": str(row["url"]) if row["url"] is not None else None,
                "branch": (
                    str(row["branch"]) if row["branch"] is not None else None
                ),
            }
            for row in rows
            if row["repo_id"]
        ]

    def list_active_job_ids(self, repo_id: str | None = None) -> list[str]:
        """Lista jobs activos (queued/running), opcionalmente filtrados por repo."""
        normalized_repo_id = (repo_id or "").strip()
        if normalized_repo_id:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT id FROM {POSTGRES_JOBS_TABLE}
                    WHERE status IN (%s, %s) AND repo_id = %s
                    ORDER BY created_at ASC
                    """,
                    (JobStatus.queued.value, JobStatus.running.value, normalized_repo_id),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT id FROM {POSTGRES_JOBS_TABLE}
                    WHERE status IN (%s, %s)
                    ORDER BY created_at ASC
                    """,
                    (JobStatus.queued.value, JobStatus.running.value),
                ).fetchall()
        return [str(row["id"]) for row in rows if row["id"]]

    def upsert_repo_runtime(
        self,
        *,
        repo_id: str,
        organization: str | None,
        repo_url: str,
        branch: str,
        local_path: str,
        embedding_provider: str | None,
        embedding_model: str | None,
    ) -> None:
        """Inserta o actualiza metadata runtime por repositorio."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {POSTGRES_REPOS_TABLE} (
                    id, organization, url, branch, local_path, created_at,
                    updated_at, embedding_provider, embedding_model
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    organization = EXCLUDED.organization,
                    url = EXCLUDED.url,
                    branch = EXCLUDED.branch,
                    local_path = EXCLUDED.local_path,
                    updated_at = EXCLUDED.updated_at,
                    embedding_provider = EXCLUDED.embedding_provider,
                    embedding_model = EXCLUDED.embedding_model
                """,
                (
                    repo_id,
                    organization,
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
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT embedding_provider, embedding_model
                FROM {POSTGRES_REPOS_TABLE}
                WHERE id = %s
                """,
                (repo_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "last_embedding_provider": row["embedding_provider"],
            "last_embedding_model": row["embedding_model"],
        }

    def delete_repo_runtime(self, repo_id: str) -> int:
        """Elimina metadata runtime del repositorio y devuelve filas afectadas."""
        with self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM {POSTGRES_REPOS_TABLE} WHERE id = %s",
                (repo_id,),
            )
            return int(cursor.rowcount or 0)

    def delete_repo_jobs(self, repo_id: str) -> int:
        """Elimina historial de jobs asociados al repositorio y devuelve filas."""
        with self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM {POSTGRES_JOBS_TABLE} WHERE repo_id = %s",
                (repo_id,),
            )
            return int(cursor.rowcount or 0)

    def delete_repo_data(self, repo_id: str) -> dict[str, int]:
        """Elimina metadata de repositorio y jobs, retornando conteos por tabla."""
        jobs_deleted = self.delete_repo_jobs(repo_id)
        repos_deleted = self.delete_repo_runtime(repo_id)
        return {
            "jobs_deleted": jobs_deleted,
            "repos_deleted": repos_deleted,
            "total": jobs_deleted + repos_deleted,
        }

    def reset_all(self) -> None:
        """Elimina todos los jobs y repos. Usar solo en reset global."""
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {POSTGRES_JOBS_TABLE}")
            conn.execute(f"DELETE FROM {POSTGRES_REPOS_TABLE}")
