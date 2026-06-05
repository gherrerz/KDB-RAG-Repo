"""Agrega tabla histórica de snapshots operativos de ingesta."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from coderag.storage.postgres_schema import POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME


revision = "0004_add_ingestion_snapshots_table"
down_revision = "0003_add_repo_last_queried_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Crea la tabla histórica de snapshots operativos por job de ingesta."""
    op.create_table(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("snapshot_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("job_status", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable_error", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("workspace_retained", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("workspace_cleanup_attempted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("workspace_cleanup_succeeded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("clone_ms", sa.Float(), nullable=True),
        sa.Column("scan_ms", sa.Float(), nullable=True),
        sa.Column("chunk_ms", sa.Float(), nullable=True),
        sa.Column("vector_total_ms", sa.Float(), nullable=True),
        sa.Column("lexical_ms", sa.Float(), nullable=True),
        sa.Column("graph_ms", sa.Float(), nullable=True),
        sa.Column("readiness_ms", sa.Float(), nullable=True),
        sa.Column("ingestion_total_ms", sa.Float(), nullable=True),
        sa.Column("files_visited", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("files_scanned", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("excluded_dir_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("excluded_extension_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("excluded_file_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("excluded_size_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("excluded_decode_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("excluded_pattern_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("visited_dirs", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pruned_dirs", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("symbols_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("chunks_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("languages_detected_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_collections_written", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_initial_batch_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_effective_batch_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_split_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_recovered_retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_payload_too_large_events", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_proxy_reset_events", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_upstream_restarting_events", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vector_documents_written", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("semantic_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("semantic_status", sa.Text(), nullable=True),
        sa.Column("semantic_relations_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("semantic_unresolved_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "idx_ingestion_snapshots_repo_snapshot",
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        ["repo_id", "snapshot_at"],
        unique=False,
    )
    op.create_index(
        "idx_ingestion_snapshots_job_id",
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    """Elimina la tabla histórica de snapshots operativos."""
    op.drop_index("idx_ingestion_snapshots_job_id", table_name=POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME)
    op.drop_index("idx_ingestion_snapshots_repo_snapshot", table_name=POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME)
    op.drop_table(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME)