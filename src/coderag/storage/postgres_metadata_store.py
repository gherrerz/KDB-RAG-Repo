"""Almacén de metadatos operativos sobre PostgreSQL."""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

from sqlalchemy import case, delete, func, literal, select, update, union
from sqlalchemy.dialects.postgresql import insert

from coderag.core.models import JobInfo, JobStatus
from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.postgres_models import (
    IngestionSnapshotRecord,
    JobRecord,
    RepoRecord,
)
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


def _normalize_datetime_offset(value: str) -> str:
    """Normaliza offsets timezone abreviados a formato ISO completo."""
    normalized = value.strip().replace(" ", "T", 1)
    if normalized.endswith("Z"):
        return normalized[:-1] + "+00:00"
    if re.search(r"[+-]\d{2}$", normalized):
        return normalized + ":00"
    return normalized


def _coerce_datetime_value(value: Any) -> datetime.datetime | None:
    """Convierte timestamps runtime a datetime timezone-aware cuando aplica."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str) and value.strip():
        normalized = _normalize_datetime_offset(value)
        try:
            return datetime.datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _safe_int(value: Any) -> int:
    """Convierte métricas heterogéneas a entero sin lanzar excepciones."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    """Convierte métricas heterogéneas a float opcional."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _snapshot_record_to_payload(
    record: IngestionSnapshotRecord,
) -> dict[str, object | None]:
    """Convierte una fila ORM de snapshot al payload público operativo."""
    return {
        "snapshot_id": int(record.id),
        "repo_id": record.repo_id,
        "job_id": record.job_id,
        "snapshot_at": record.snapshot_at,
        "job_status": record.job_status,
        "error_message": record.error_message,
        "retryable_error": bool(record.retryable_error),
        "workspace_retained": bool(record.workspace_retained),
        "workspace_cleanup_attempted": bool(
            record.workspace_cleanup_attempted
        ),
        "workspace_cleanup_succeeded": bool(
            record.workspace_cleanup_succeeded
        ),
        "clone_ms": record.clone_ms,
        "scan_ms": record.scan_ms,
        "chunk_ms": record.chunk_ms,
        "vector_total_ms": record.vector_total_ms,
        "lexical_ms": record.lexical_ms,
        "graph_ms": record.graph_ms,
        "readiness_ms": record.readiness_ms,
        "ingestion_total_ms": record.ingestion_total_ms,
        "files_visited": int(record.files_visited),
        "files_scanned": int(record.files_scanned),
        "excluded_dir_count": int(record.excluded_dir_count),
        "excluded_extension_count": int(record.excluded_extension_count),
        "excluded_file_count": int(record.excluded_file_count),
        "excluded_size_count": int(record.excluded_size_count),
        "excluded_decode_count": int(record.excluded_decode_count),
        "excluded_pattern_count": int(record.excluded_pattern_count),
        "visited_dirs": int(record.visited_dirs),
        "pruned_dirs": int(record.pruned_dirs),
        "symbols_count": int(record.symbols_count),
        "chunks_count": int(record.chunks_count),
        "languages_detected_count": int(record.languages_detected_count),
        "vector_collections_written": int(record.vector_collections_written),
        "vector_initial_batch_size": int(record.vector_initial_batch_size),
        "vector_effective_batch_size": int(record.vector_effective_batch_size),
        "vector_split_count": int(record.vector_split_count),
        "vector_recovered_retry_count": int(
            record.vector_recovered_retry_count
        ),
        "vector_payload_too_large_events": int(
            record.vector_payload_too_large_events
        ),
        "vector_proxy_reset_events": int(record.vector_proxy_reset_events),
        "vector_upstream_restarting_events": int(
            record.vector_upstream_restarting_events
        ),
        "vector_documents_written": int(record.vector_documents_written),
        "semantic_enabled": bool(record.semantic_enabled),
        "semantic_status": record.semantic_status,
        "semantic_relations_count": int(record.semantic_relations_count),
        "semantic_unresolved_count": int(record.semantic_unresolved_count),
    }


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
            last_queried_at=None,
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
            RepoRecord.last_queried_at,
        ).where(RepoRecord.id == repo_id)
        with self._session_factory.get_session() as session:
            row = session.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        last_queried_at = _coerce_datetime_value(row["last_queried_at"])
        return {
            "last_embedding_provider": row["embedding_provider"],
            "last_embedding_model": row["embedding_model"],
            "last_queried_at": (
                last_queried_at.isoformat()
                if last_queried_at is not None
                else None
            ),
        }

    def touch_repo_last_queried_at(self, repo_id: str) -> int:
        """Actualiza la fecha de última consulta del repositorio."""
        now = datetime.datetime.now(datetime.UTC)
        statement = (
            update(RepoRecord)
            .where(RepoRecord.id == repo_id)
            .values(last_queried_at=now)
        )
        with self._session_factory.get_session() as session:
            result = session.execute(statement)
            session.commit()
        return int(result.rowcount or 0)

    def list_stale_repos(
        self,
        *,
        last_queried_on_or_before: datetime.datetime,
    ) -> list[dict[str, object | None]]:
        """Lista repositorios con última consulta vencida o inexistente."""
        statement = (
            select(
                RepoRecord.id.label("repo_id"),
                RepoRecord.organization,
                RepoRecord.url,
                RepoRecord.branch,
                RepoRecord.local_path,
                RepoRecord.created_at,
                RepoRecord.updated_at,
                RepoRecord.last_queried_at,
            )
            .where(
                (RepoRecord.last_queried_at.is_(None))
                | (RepoRecord.last_queried_at <= last_queried_on_or_before)
            )
            .order_by(RepoRecord.id.asc())
        )
        with self._session_factory.get_session() as session:
            rows = session.execute(statement).mappings().all()
        return [
            {
                "repo_id": str(row["repo_id"]),
                "organization": row["organization"],
                "url": row["url"],
                "branch": row["branch"],
                "local_path": row["local_path"],
                "created_at": _coerce_datetime_value(row["created_at"]),
                "updated_at": _coerce_datetime_value(row["updated_at"]),
                "last_queried_at": _coerce_datetime_value(
                    row["last_queried_at"]
                ),
            }
            for row in rows
            if row["repo_id"]
        ]

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

    def list_repo_ingest_snapshots(
        self,
        repo_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, object | None]]:
        """Lista snapshots operativos históricos del repo en orden descendente."""
        normalized_limit = max(int(limit), 1)
        statement = (
            select(IngestionSnapshotRecord)
            .where(IngestionSnapshotRecord.repo_id == repo_id)
            .order_by(
                IngestionSnapshotRecord.snapshot_at.desc(),
                IngestionSnapshotRecord.id.desc(),
            )
            .limit(normalized_limit)
        )
        with self._session_factory.get_session() as session:
            rows = session.execute(statement).scalars().all()
        return [_snapshot_record_to_payload(row) for row in rows]

    def delete_repo_ingest_snapshots(self, repo_id: str) -> int:
        """Elimina snapshots históricos del repositorio y devuelve filas."""
        statement = delete(IngestionSnapshotRecord).where(
            IngestionSnapshotRecord.repo_id == repo_id
        )
        with self._session_factory.get_session() as session:
            result = session.execute(statement)
            session.commit()
        return int(result.rowcount or 0)

    def delete_repo_data(self, repo_id: str) -> dict[str, int]:
        """Elimina metadata de repositorio y jobs, retornando conteos por tabla."""
        snapshots_deleted = self.delete_repo_ingest_snapshots(repo_id)
        jobs_deleted = self.delete_repo_jobs(repo_id)
        repos_deleted = self.delete_repo_runtime(repo_id)
        return {
            "snapshots_deleted": snapshots_deleted,
            "jobs_deleted": jobs_deleted,
            "repos_deleted": repos_deleted,
            "total": snapshots_deleted + jobs_deleted + repos_deleted,
        }

    def record_ingest_snapshot(
        self,
        *,
        repo_id: str,
        job_id: str,
        job_status: str,
        error_message: str | None,
        diagnostics: dict[str, object],
        snapshot_at: datetime.datetime,
    ) -> None:
        """Persiste una foto operativa por job y retiene solo las últimas N por repo."""
        scan_stats = diagnostics.get("scan_stats") if isinstance(diagnostics, dict) else {}
        vector_index = diagnostics.get("vector_index") if isinstance(diagnostics, dict) else {}
        semantic_graph = diagnostics.get("semantic_graph") if isinstance(diagnostics, dict) else {}
        coverage = diagnostics.get("coverage") if isinstance(diagnostics, dict) else {}

        if not isinstance(scan_stats, dict):
            scan_stats = {}
        if not isinstance(vector_index, dict):
            vector_index = {}
        if not isinstance(semantic_graph, dict):
            semantic_graph = {}
        if not isinstance(coverage, dict):
            coverage = {}

        insert_stmt = insert(IngestionSnapshotRecord).values(
            repo_id=repo_id,
            job_id=job_id,
            snapshot_at=snapshot_at,
            job_status=job_status,
            error_message=error_message,
            retryable_error=bool(diagnostics.get("retryable_error", False)),
            workspace_retained=bool(diagnostics.get("workspace_retained", True)),
            workspace_cleanup_attempted=bool(diagnostics.get("workspace_cleanup_attempted", False)),
            workspace_cleanup_succeeded=bool(diagnostics.get("workspace_cleanup_succeeded", False)),
            clone_ms=_safe_float(diagnostics.get("clone_ms")),
            scan_ms=_safe_float(diagnostics.get("scan_ms")),
            chunk_ms=_safe_float(diagnostics.get("chunk_ms")),
            vector_total_ms=_safe_float(diagnostics.get("vector_total_ms")),
            lexical_ms=_safe_float(diagnostics.get("lexical_ms")),
            graph_ms=_safe_float(diagnostics.get("graph_ms")),
            readiness_ms=_safe_float(diagnostics.get("readiness_ms")),
            ingestion_total_ms=_safe_float(diagnostics.get("ingestion_total_ms")),
            files_visited=_safe_int(scan_stats.get("visited")),
            files_scanned=_safe_int(scan_stats.get("scanned")),
            excluded_dir_count=_safe_int(scan_stats.get("excluded_dir")),
            excluded_extension_count=_safe_int(scan_stats.get("excluded_extension")),
            excluded_file_count=_safe_int(scan_stats.get("excluded_file")),
            excluded_size_count=_safe_int(scan_stats.get("excluded_size")),
            excluded_decode_count=_safe_int(scan_stats.get("excluded_decode")),
            excluded_pattern_count=_safe_int(scan_stats.get("excluded_pattern")),
            visited_dirs=_safe_int(scan_stats.get("visited_dirs")),
            pruned_dirs=_safe_int(scan_stats.get("pruned_dirs")),
            symbols_count=_safe_int(coverage.get("chunks")),
            chunks_count=_safe_int(coverage.get("chunks")),
            languages_detected_count=_safe_int(len(coverage.get("languages", {})))
            if isinstance(coverage.get("languages", {}), dict)
            else 0,
            vector_collections_written=_safe_int(vector_index.get("collections_written")),
            vector_initial_batch_size=_safe_int(vector_index.get("initial_batch_size")),
            vector_effective_batch_size=_safe_int(vector_index.get("effective_batch_size")),
            vector_split_count=_safe_int(vector_index.get("split_count")),
            vector_recovered_retry_count=_safe_int(vector_index.get("recovered_retry_count")),
            vector_payload_too_large_events=_safe_int(vector_index.get("payload_too_large_events")),
            vector_proxy_reset_events=_safe_int(vector_index.get("proxy_reset_events")),
            vector_upstream_restarting_events=_safe_int(vector_index.get("upstream_restarting_events")),
            vector_documents_written=_safe_int(vector_index.get("documents_written")),
            semantic_enabled=bool(semantic_graph.get("enabled", False)),
            semantic_status=(str(semantic_graph.get("status")) if semantic_graph.get("status") is not None else None),
            semantic_relations_count=_safe_int(semantic_graph.get("relation_counts")),
            semantic_unresolved_count=_safe_int(semantic_graph.get("unresolved_count")),
        )

        retention_limit = 50
        with self._session_factory.get_session() as session:
            session.execute(insert_stmt)
            retention_subquery = (
                select(IngestionSnapshotRecord.id)
                .where(IngestionSnapshotRecord.repo_id == repo_id)
                .order_by(
                    IngestionSnapshotRecord.snapshot_at.desc(),
                    IngestionSnapshotRecord.id.desc(),
                )
                .offset(retention_limit)
            )
            session.execute(
                delete(IngestionSnapshotRecord).where(
                    IngestionSnapshotRecord.id.in_(retention_subquery)
                )
            )
            session.commit()

    def reset_all(self) -> None:
        """Elimina todos los jobs y repos. Usar solo en reset global."""
        with self._session_factory.get_session() as session:
            session.execute(delete(IngestionSnapshotRecord))
            session.execute(delete(JobRecord))
            session.execute(delete(RepoRecord))
            session.commit()