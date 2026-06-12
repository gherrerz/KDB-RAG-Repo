"""Agrega modo de ingesta y commits al snapshot operativo histórico."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from coderag.storage.postgres_schema import POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME


revision = "0007_add_snapshot_ingest_mode_and_commits"
down_revision = "0006_add_repo_last_indexed_commit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Agrega columnas de observabilidad de la ingesta incremental al snapshot."""
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column("ingest_mode", sa.Text(), nullable=True),
    )
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column("ingest_mode_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column("base_commit", sa.Text(), nullable=True),
    )
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column("head_commit", sa.Text(), nullable=True),
    )
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column(
            "changed_files_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column(
            "deleted_files_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    """Elimina las columnas de observabilidad incremental del snapshot."""
    op.drop_column(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME, "deleted_files_count")
    op.drop_column(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME, "changed_files_count")
    op.drop_column(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME, "head_commit")
    op.drop_column(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME, "base_commit")
    op.drop_column(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME, "ingest_mode_reason")
    op.drop_column(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME, "ingest_mode")
