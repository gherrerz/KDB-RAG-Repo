"""Retira tablas PostgreSQL legacy tras cerrar la observacion."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_drop_legacy_postgres_tables"
down_revision = "0001_initial_postgres_schema"
branch_labels = None
depends_on = None


LEGACY_JOBS_TABLE_NAME = "jobs"
LEGACY_REPOS_TABLE_NAME = "repos"
LEGACY_LEXICAL_CORPUS_TABLE_NAME = "lexical_corpus"


def upgrade() -> None:
    """Elimina tablas legacy retenidas cuando todavia existen."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name in (
        LEGACY_LEXICAL_CORPUS_TABLE_NAME,
        LEGACY_REPOS_TABLE_NAME,
        LEGACY_JOBS_TABLE_NAME,
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)


def downgrade() -> None:
    """Recrea tablas legacy vacias para rollback estructural excepcional."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if LEGACY_JOBS_TABLE_NAME not in existing_tables:
        op.create_table(
            LEGACY_JOBS_TABLE_NAME,
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("progress", sa.Float(), nullable=False),
            sa.Column("logs", sa.Text(), nullable=False),
            sa.Column("repo_id", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("diagnostics", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if LEGACY_REPOS_TABLE_NAME not in existing_tables:
        op.create_table(
            LEGACY_REPOS_TABLE_NAME,
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("organization", sa.Text(), nullable=True),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("branch", sa.Text(), nullable=False),
            sa.Column("local_path", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=True),
            sa.Column("embedding_provider", sa.Text(), nullable=True),
            sa.Column("embedding_model", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if LEGACY_LEXICAL_CORPUS_TABLE_NAME not in existing_tables:
        op.create_table(
            LEGACY_LEXICAL_CORPUS_TABLE_NAME,
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("repo_id", sa.Text(), nullable=False),
            sa.Column("doc", sa.Text(), nullable=False),
            sa.Column("path", sa.Text(), nullable=True),
            sa.Column("symbol_name", sa.Text(), nullable=True),
            sa.Column("entity_type", sa.Text(), nullable=True),
            sa.Column("metadata", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint("repo_id", "id", name="pk_legacy_lexical_corpus"),
        )