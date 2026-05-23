"""Almacén de metadatos operativos sobre PostgreSQL."""

from __future__ import annotations

import datetime
import json
from typing import Any

from sqlalchemy import case, delete, literal, select, update, union
from sqlalchemy.dialects.postgresql import insert

from coderag.core.models import JobInfo, JobStatus
from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.postgres_models import JobRecord, RepoRecord
from coderag.storage.postgres_session import PostgresSessionFactory


def _coerce_diagnostics(value: Any) -> dict[str, Any]:
    """Normaliza diagnostics para filas JSONB y datos legacy serializados."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _job_record_to_job_info(record: JobRecord) -> JobInfo:
    """Convierte un registro ORM de jobs al modelo de dominio expuesto."""
    logs = record.logs.splitlines() if record.logs else []
    diagnostics = _coerce_diagnostics(record.diagnostics)
    return JobInfo(
        id=record.id,
        status=JobStatus(record.status),
        progress=float(record.progress),
        logs=logs,
        repo_id=record.repo_id,
        error=record.error,
        diagnostics=diagnostics,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class PostgresMetadataStore(BaseMetadataStore):
    """Implementación de BaseMetadataStore usando SQLAlchemy sobre PostgreSQL."""

    def __init__(
        self,
        postgres_dsn: str,
        *,
        session_factory: PostgresSessionFactory | None = None,
    ) -> None:
        """Crea la instancia usando un factory de sesiones reutilizable."""
        self._url = postgres_dsn
        self._session_factory = session_factory or PostgresSessionFactory(
            postgres_dsn
        )

    def upsert_job(self, job: JobInfo) -> None:
        """Inserta o actualiza la instantánea persistida del trabajo."""
        insert_stmt = insert(JobRecord).values(
            id=job.id,
            status=job.status.value,
            progress=job.progress,
            logs="\n".join(job.logs),
            repo_id=job.repo_id,
            error=job.error,
            diagnostics=job.diagnostics or {},
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        statement = insert_stmt.on_conflict_do_update(
            index_elements=[JobRecord.id],
            set_={
                "status": insert_stmt.excluded.status,
                "progress": insert_stmt.excluded.progress,
                "logs": insert_stmt.excluded.logs,
                "repo_id": insert_stmt.excluded.repo_id,
                "error": insert_stmt.excluded.error,
                "diagnostics": insert_stmt.excluded.diagnostics,
                "updated_at": insert_stmt.excluded.updated_at,
            },
        )
        with self._session_factory.get_session() as session:
            session.execute(statement)
            session.commit()

    def recover_interrupted_jobs(self) -> int:
        """Marca jobs queued/running como failed tras reinicio inesperado."""
        reason = (
            "Job interrumpido por reinicio del servicio. "
            "Reintenta la ingesta."
        )
        now = datetime.datetime.now(datetime.UTC)
        statement = (
            update(JobRecord)
            .where(
                JobRecord.status.in_(
                    [JobStatus.queued.value, JobStatus.running.value]
                )
            )
            .values(
                status=JobStatus.failed.value,
                error=case(
                    (JobRecord.error.is_(None), literal(reason)),
                    (JobRecord.error == "", literal(reason)),
                    else_=JobRecord.error,
                ),
                logs=case(
                    (JobRecord.logs.is_(None), literal(reason)),
                    (JobRecord.logs == "", literal(reason)),
                    else_=JobRecord.logs + literal("\n") + literal(reason),
                ),
                updated_at=now,
            )
        )
        with self._session_factory.get_session() as session:
            result = session.execute(statement)
            session.commit()
        return int(result.rowcount or 0)

    def get_job(self, job_id: str) -> JobInfo | None:
        """Lee la instantánea del trabajo por identificador."""
        with self._session_factory.get_session() as session:
            record = session.get(JobRecord, job_id)
        if record is None:
            return None
        return _job_record_to_job_info(record)

    def list_repo_ids(self) -> list[str]:
        """Lista ids de repositorio conocidos desde tablas de jobs y repos."""
        repo_statement = select(RepoRecord.id.label("repo_id"))
        jobs_statement = select(JobRecord.repo_id.label("repo_id")).where(
            JobRecord.repo_id.is_not(None),
            JobRecord.repo_id != "",
        )
        statement = union(repo_statement, jobs_statement).order_by("repo_id")
        with self._session_factory.get_session() as session:
            repo_ids = session.execute(statement).scalars().all()
        return [str(repo_id) for repo_id in repo_ids if repo_id]

    def list_repo_catalog(self) -> list[dict[str, str | None]]:
        """Retorna catálogo de repos persistidos con metadata de ingesta."""
        statement = select(
            RepoRecord.id.label("repo_id"),
            RepoRecord.organization,
            RepoRecord.url,
            RepoRecord.branch,
        ).order_by(RepoRecord.id.asc())
        with self._session_factory.get_session() as session:
            rows = session.execute(statement).mappings().all()
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
        statement = select(JobRecord.id).where(
            JobRecord.status.in_(
                [JobStatus.queued.value, JobStatus.running.value]
            )
        )
        normalized_repo_id = (repo_id or "").strip()
        if normalized_repo_id:
            statement = statement.where(JobRecord.repo_id == normalized_repo_id)
        statement = statement.order_by(JobRecord.created_at.asc())

        with self._session_factory.get_session() as session:
            job_ids = session.execute(statement).scalars().all()
        return [str(job_id) for job_id in job_ids if job_id]

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
        now = datetime.datetime.now(datetime.UTC)
        insert_stmt = insert(RepoRecord).values(
            id=repo_id,
            organization=organization,
            url=repo_url,
            branch=branch,
            local_path=local_path,
            created_at=now,
            updated_at=now,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
        )
        statement = insert_stmt.on_conflict_do_update(
            index_elements=[RepoRecord.id],
            set_={
                "organization": insert_stmt.excluded.organization,
                "url": insert_stmt.excluded.url,
                "branch": insert_stmt.excluded.branch,
                "local_path": insert_stmt.excluded.local_path,
                "updated_at": insert_stmt.excluded.updated_at,
                "embedding_provider": insert_stmt.excluded.embedding_provider,
                "embedding_model": insert_stmt.excluded.embedding_model,
            },
        )
        with self._session_factory.get_session() as session:
            session.execute(statement)
            session.commit()

    def get_repo_runtime(self, repo_id: str) -> dict[str, str | None] | None:
        """Obtiene metadata runtime almacenada para un repositorio."""
        statement = select(
            RepoRecord.embedding_provider,
            RepoRecord.embedding_model,
        ).where(RepoRecord.id == repo_id)
        with self._session_factory.get_session() as session:
            row = session.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        return {
            "last_embedding_provider": row["embedding_provider"],
            "last_embedding_model": row["embedding_model"],
        }

    def delete_repo_runtime(self, repo_id: str) -> int:
        """Elimina metadata runtime del repositorio y devuelve filas afectadas."""
        statement = delete(RepoRecord).where(RepoRecord.id == repo_id)
        with self._session_factory.get_session() as session:
            result = session.execute(statement)
            session.commit()
        return int(result.rowcount or 0)

    def delete_repo_jobs(self, repo_id: str) -> int:
        """Elimina historial de jobs asociados al repositorio y devuelve filas."""
        statement = delete(JobRecord).where(JobRecord.repo_id == repo_id)
        with self._session_factory.get_session() as session:
            result = session.execute(statement)
            session.commit()
        return int(result.rowcount or 0)

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
        with self._session_factory.get_session() as session:
            session.execute(delete(JobRecord))
            session.execute(delete(RepoRecord))
            session.commit()