"""Modelos ORM de SQLAlchemy para metadata operativa en PostgreSQL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from coderag.storage.postgres_schema import (
    POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
    POSTGRES_JOBS_TABLE_NAME,
    POSTGRES_REPOS_TABLE_NAME,
    PostgresDeclarativeBase,
)


class JobRecord(PostgresDeclarativeBase):
    """Representa la metadata persistida de un job de ingesta."""

    __tablename__ = POSTGRES_JOBS_TABLE_NAME

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False)
    logs: Mapped[str] = mapped_column(Text, nullable=False)
    repo_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )


class RepoRecord(PostgresDeclarativeBase):
    """Representa la metadata runtime persistida de un repositorio."""

    __tablename__ = POSTGRES_REPOS_TABLE_NAME

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    last_queried_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    embedding_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_indexed_commit: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestionSnapshotRecord(PostgresDeclarativeBase):
    """Representa una foto operativa histórica de una ingesta por job."""

    __tablename__ = POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    snapshot_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    job_status: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    workspace_retained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    workspace_cleanup_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    workspace_cleanup_succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    clone_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    scan_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    chunk_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    vector_total_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    lexical_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    graph_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    readiness_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingestion_total_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    repo_size_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    files_visited: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_dir_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_extension_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_size_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_decode_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_pattern_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visited_dirs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pruned_dirs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbols_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    languages_detected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_collections_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_initial_batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_effective_batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_split_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_recovered_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_payload_too_large_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_proxy_reset_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_upstream_restarting_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_documents_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_tokens_read_estimated: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    semantic_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    semantic_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_relations_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    semantic_unresolved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingest_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_mode_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_files_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_files_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)