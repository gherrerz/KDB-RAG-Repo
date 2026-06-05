"""Metadata SQLAlchemy compartida para el esquema PostgreSQL operativo."""

from __future__ import annotations

from sqlalchemy import Index, MetaData, PrimaryKeyConstraint, Table
from sqlalchemy import Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.schema import Column

from coderag.storage.postgres_table_names import (
    POSTGRES_INGESTION_SNAPSHOTS_TABLE,
    POSTGRES_JOBS_TABLE,
    POSTGRES_LEXICAL_CORPUS_TABLE,
    POSTGRES_REPOS_TABLE,
)


def _physical_table_name(name: str) -> str:
    """Normaliza nombres legacy al identificador físico esperado en Postgres."""
    return name.strip().lower()


POSTGRES_JOBS_TABLE_NAME = _physical_table_name(POSTGRES_JOBS_TABLE)
POSTGRES_REPOS_TABLE_NAME = _physical_table_name(POSTGRES_REPOS_TABLE)
POSTGRES_LEXICAL_CORPUS_TABLE_NAME = _physical_table_name(
    POSTGRES_LEXICAL_CORPUS_TABLE
)
POSTGRES_INGESTION_SNAPSHOTS_TABLE_NAME = _physical_table_name(
    POSTGRES_INGESTION_SNAPSHOTS_TABLE
)


POSTGRES_SCHEMA_METADATA = MetaData()


class PostgresDeclarativeBase(DeclarativeBase):
    """Base declarativa compartida por los modelos ORM de Postgres."""

    metadata = POSTGRES_SCHEMA_METADATA


lexical_corpus_table = Table(
    POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
    POSTGRES_SCHEMA_METADATA,
    Column("id", Text, nullable=False),
    Column("repo_id", Text, nullable=False),
    Column("doc", Text, nullable=False),
    Column("path", Text, nullable=True),
    Column("symbol_name", Text, nullable=True),
    Column("entity_type", Text, nullable=True),
    Column("metadata", JSONB, nullable=True),
    Column("fts_vector", TSVECTOR, nullable=True),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("repo_id", "id", name="pk_lexical_corpus"),
    Index(
        "idx_lexical_fts",
        "fts_vector",
        postgresql_using="gin",
    ),
    Index("idx_lexical_repo", "repo_id"),
)