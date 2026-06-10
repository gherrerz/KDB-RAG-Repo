"""Agrega tamaño de repo y tokens estimados a snapshots operativos."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from coderag.storage.postgres_schema import POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME


revision = "0005_add_snapshot_repo_size_and_embedding_tokens"
down_revision = "0004_add_ingestion_snapshots_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Agrega tamaño leído del repo y tokens estimados al snapshot histórico."""
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column("repo_size_mb", sa.Float(), nullable=True),
    )
    op.add_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        sa.Column(
            "embedding_tokens_read_estimated",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    """Elimina tamaño leído del repo y tokens estimados del snapshot."""
    op.drop_column(
        POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME,
        "embedding_tokens_read_estimated",
    )
    op.drop_column(POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME, "repo_size_mb")