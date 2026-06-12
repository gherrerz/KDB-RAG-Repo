"""Agrega last_indexed_commit a metadata runtime de repositorios."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from coderag.storage.postgres_schema import POSTGRES_REPOS_TABLE_NAME


revision = "0006_add_repo_last_indexed_commit"
down_revision = "0005_add_snapshot_repo_size_and_embedding_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Agrega la columna del último commit indexado a la tabla runtime de repos."""
    op.add_column(
        POSTGRES_REPOS_TABLE_NAME,
        sa.Column(
            "last_indexed_commit",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Elimina la columna del último commit indexado en rollback estructural."""
    op.drop_column(POSTGRES_REPOS_TABLE_NAME, "last_indexed_commit")
