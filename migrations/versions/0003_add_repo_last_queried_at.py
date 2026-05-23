"""Agrega last_queried_at a metadata runtime de repositorios."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from coderag.storage.postgres_schema import POSTGRES_REPOS_TABLE_NAME


revision = "0003_add_repo_last_queried_at"
down_revision = "0002_drop_legacy_postgres_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Agrega la columna de última consulta a la tabla runtime de repos."""
    op.add_column(
        POSTGRES_REPOS_TABLE_NAME,
        sa.Column(
            "last_queried_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Elimina la columna de última consulta en rollback estructural."""
    op.drop_column(POSTGRES_REPOS_TABLE_NAME, "last_queried_at")