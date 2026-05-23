"""Pruebas unitarias para metadata y modelos ORM de PostgreSQL."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR

from coderag.storage.postgres_models import JobRecord, RepoRecord
from coderag.storage.postgres_schema import (
    POSTGRES_JOBS_TABLE_NAME,
    POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
    POSTGRES_REPOS_TABLE_NAME,
    lexical_corpus_table,
)


def test_job_and_repo_models_use_lowercase_physical_table_names() -> None:
    """Los modelos deben mapear a los nombres físicos legacy en minúsculas."""
    assert JobRecord.__tablename__ == POSTGRES_JOBS_TABLE_NAME
    assert RepoRecord.__tablename__ == POSTGRES_REPOS_TABLE_NAME
    assert JobRecord.__tablename__ == "tbl_repository_jobs"
    assert RepoRecord.__tablename__ == "tbl_repository_repos"


def test_job_model_uses_jsonb_and_timestamptz_columns() -> None:
    """La metadata de jobs debe usar tipos Postgres modernos."""
    diagnostics_type = JobRecord.__table__.c["diagnostics"].type
    created_at_type = JobRecord.__table__.c["created_at"].type
    updated_at_type = JobRecord.__table__.c["updated_at"].type

    assert isinstance(diagnostics_type, JSONB)
    assert isinstance(created_at_type, TIMESTAMP)
    assert created_at_type.timezone is True
    assert isinstance(updated_at_type, TIMESTAMP)
    assert updated_at_type.timezone is True


def test_repo_model_uses_timestamptz_columns() -> None:
    """La metadata de repos debe persistir timestamps con timezone."""
    created_at_type = RepoRecord.__table__.c["created_at"].type
    updated_at_type = RepoRecord.__table__.c["updated_at"].type
    last_queried_at_type = RepoRecord.__table__.c["last_queried_at"].type

    assert isinstance(created_at_type, TIMESTAMP)
    assert created_at_type.timezone is True
    assert isinstance(updated_at_type, TIMESTAMP)
    assert updated_at_type.timezone is True
    assert isinstance(last_queried_at_type, TIMESTAMP)
    assert last_queried_at_type.timezone is True


def test_lexical_table_uses_jsonb_tsvector_and_expected_indexes() -> None:
    """La tabla lexical debe quedar representada en metadata compartida."""
    assert lexical_corpus_table.name == POSTGRES_LEXICAL_CORPUS_TABLE_NAME
    assert lexical_corpus_table.name == "tbl_repository_lexicalcorpus"
    assert isinstance(lexical_corpus_table.c["metadata"].type, JSONB)
    assert isinstance(lexical_corpus_table.c["fts_vector"].type, TSVECTOR)

    index_names = sorted(index.name for index in lexical_corpus_table.indexes)
    assert index_names == ["idx_lexical_fts", "idx_lexical_repo"]

    primary_key_columns = [column.name for column in lexical_corpus_table.primary_key]
    assert primary_key_columns == ["repo_id", "id"]