"""Esquema inicial para metadata y corpus léxico en PostgreSQL."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from coderag.storage.postgres_schema import (
    POSTGRES_JOBS_TABLE_NAME,
    POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
    POSTGRES_REPOS_TABLE_NAME,
)


revision = "0001_initial_postgres_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Crea el esquema inicial operativo de Postgres."""
    op.create_table(
        POSTGRES_JOBS_TABLE_NAME,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("logs", sa.Text(), nullable=False),
        sa.Column("repo_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        POSTGRES_REPOS_TABLE_NAME,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("embedding_provider", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("repo_id", sa.Text(), nullable=False),
        sa.Column("doc", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("symbol_name", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fts_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("repo_id", "id", name="pk_lexical_corpus"),
    )
    op.create_index(
        "idx_lexical_fts",
        POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
        ["fts_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "idx_lexical_repo",
        POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
        ["repo_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revierte el esquema inicial de Postgres."""
    op.drop_index("idx_lexical_repo", table_name=POSTGRES_LEXICAL_CORPUS_TABLE_NAME)
    op.drop_index("idx_lexical_fts", table_name=POSTGRES_LEXICAL_CORPUS_TABLE_NAME)
    op.drop_table(POSTGRES_LEXICAL_CORPUS_TABLE_NAME)
    op.drop_table(POSTGRES_REPOS_TABLE_NAME)
    op.drop_table(POSTGRES_JOBS_TABLE_NAME)