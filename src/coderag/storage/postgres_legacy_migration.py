"""Migración de datos PostgreSQL legacy al esquema Alembic actual."""

from __future__ import annotations

from typing import Any, TypedDict

from sqlalchemy import inspect, text as sql_text

from coderag.core.settings import resolve_postgres_dsn
from coderag.storage.postgres_schema import (
    POSTGRES_JOBS_TABLE_NAME,
    POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
    POSTGRES_REPOS_TABLE_NAME,
)
from coderag.storage.postgres_schema_admin import run_postgres_schema_command
from coderag.storage.postgres_session import PostgresSessionFactory


LEGACY_JOBS_TABLE_NAME = "jobs"
LEGACY_REPOS_TABLE_NAME = "repos"
LEGACY_LEXICAL_CORPUS_TABLE_NAME = "lexical_corpus"

_LEGACY_REQUIRED_COLUMNS: dict[str, set[str]] = {
    LEGACY_JOBS_TABLE_NAME: {
        "id",
        "status",
        "progress",
        "logs",
        "repo_id",
        "error",
        "diagnostics",
        "created_at",
        "updated_at",
    },
    LEGACY_REPOS_TABLE_NAME: {
        "id",
        "organization",
        "url",
        "branch",
        "local_path",
        "created_at",
        "updated_at",
        "embedding_provider",
        "embedding_model",
    },
    LEGACY_LEXICAL_CORPUS_TABLE_NAME: {
        "id",
        "repo_id",
        "doc",
        "path",
        "symbol_name",
        "entity_type",
        "metadata",
        "created_at",
    },
}

_MIGRATE_LEGACY_JOBS = sql_text(
    f"""
    INSERT INTO {POSTGRES_JOBS_TABLE_NAME} (
        id,
        status,
        progress,
        logs,
        repo_id,
        error,
        diagnostics,
        created_at,
        updated_at
    )
    SELECT
        id,
        status,
        progress,
        logs,
        NULLIF(repo_id, ''),
        error,
        CASE
            WHEN diagnostics IS NULL OR BTRIM(diagnostics) = '' THEN NULL
            ELSE diagnostics::jsonb
        END,
        created_at::timestamptz,
        updated_at::timestamptz
    FROM {LEGACY_JOBS_TABLE_NAME}
    ON CONFLICT (id) DO UPDATE SET
        status = EXCLUDED.status,
        progress = EXCLUDED.progress,
        logs = EXCLUDED.logs,
        repo_id = EXCLUDED.repo_id,
        error = EXCLUDED.error,
        diagnostics = EXCLUDED.diagnostics,
        updated_at = EXCLUDED.updated_at
    """
)

_MIGRATE_LEGACY_REPOS = sql_text(
    f"""
    INSERT INTO {POSTGRES_REPOS_TABLE_NAME} (
        id,
        organization,
        url,
        branch,
        local_path,
        created_at,
        updated_at,
        embedding_provider,
        embedding_model
    )
    SELECT
        id,
        organization,
        url,
        branch,
        local_path,
        created_at::timestamptz,
        CASE
            WHEN updated_at IS NULL OR BTRIM(updated_at) = '' THEN NULL
            ELSE updated_at::timestamptz
        END,
        embedding_provider,
        embedding_model
    FROM {LEGACY_REPOS_TABLE_NAME}
    ON CONFLICT (id) DO UPDATE SET
        organization = EXCLUDED.organization,
        url = EXCLUDED.url,
        branch = EXCLUDED.branch,
        local_path = EXCLUDED.local_path,
        updated_at = EXCLUDED.updated_at,
        embedding_provider = EXCLUDED.embedding_provider,
        embedding_model = EXCLUDED.embedding_model
    """
)

_MIGRATE_LEGACY_LEXICAL = sql_text(
    f"""
    INSERT INTO {POSTGRES_LEXICAL_CORPUS_TABLE_NAME} (
        id,
        repo_id,
        doc,
        path,
        symbol_name,
        entity_type,
        metadata,
        fts_vector,
        created_at
    )
    SELECT
        id,
        repo_id,
        doc,
        path,
        symbol_name,
        entity_type,
        CASE
            WHEN metadata IS NULL OR BTRIM(metadata) = '' THEN NULL
            ELSE metadata::jsonb
        END,
        setweight(to_tsvector(:lang, COALESCE(symbol_name, '')), 'A')
        || setweight(to_tsvector(:lang, COALESCE(path, '')), 'B')
        || setweight(to_tsvector(:lang, COALESCE(doc, '')), 'C'),
        created_at::timestamptz
    FROM {LEGACY_LEXICAL_CORPUS_TABLE_NAME}
    ON CONFLICT (repo_id, id) DO UPDATE SET
        doc = EXCLUDED.doc,
        path = EXCLUDED.path,
        symbol_name = EXCLUDED.symbol_name,
        entity_type = EXCLUDED.entity_type,
        metadata = EXCLUDED.metadata,
        fts_vector = EXCLUDED.fts_vector
    """
)

_COUNT_ROWS = "SELECT count(*) FROM {table_name}"

_COUNT_MISSING_JOBS = sql_text(
    f"""
    SELECT count(*)
    FROM {LEGACY_JOBS_TABLE_NAME} AS legacy
    LEFT JOIN {POSTGRES_JOBS_TABLE_NAME} AS current
        ON current.id = legacy.id
    WHERE current.id IS NULL
    """
)

_COUNT_MISSING_REPOS = sql_text(
    f"""
    SELECT count(*)
    FROM {LEGACY_REPOS_TABLE_NAME} AS legacy
    LEFT JOIN {POSTGRES_REPOS_TABLE_NAME} AS current
        ON current.id = legacy.id
    WHERE current.id IS NULL
    """
)

_COUNT_MISSING_LEXICAL = sql_text(
    f"""
    SELECT count(*)
    FROM {LEGACY_LEXICAL_CORPUS_TABLE_NAME} AS legacy
    LEFT JOIN {POSTGRES_LEXICAL_CORPUS_TABLE_NAME} AS current
        ON current.repo_id = legacy.repo_id
        AND current.id = legacy.id
    WHERE current.id IS NULL
    """
)


class TableAudit(TypedDict):
    """Auditoría de conteos source/target para una tabla migrada."""

    source_count: int
    target_count_before: int
    target_count_after: int
    missing_after: int
    matched_after: int


_TABLE_NAME_MAP = {
    LEGACY_JOBS_TABLE_NAME: POSTGRES_JOBS_TABLE_NAME,
    LEGACY_REPOS_TABLE_NAME: POSTGRES_REPOS_TABLE_NAME,
    LEGACY_LEXICAL_CORPUS_TABLE_NAME: POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
}

_MISSING_COUNT_BY_TABLE = {
    LEGACY_JOBS_TABLE_NAME: _COUNT_MISSING_JOBS,
    LEGACY_REPOS_TABLE_NAME: _COUNT_MISSING_REPOS,
    LEGACY_LEXICAL_CORPUS_TABLE_NAME: _COUNT_MISSING_LEXICAL,
}


def _count_rows(connection: Any, table_name: str) -> int:
    """Cuenta filas de una tabla arbitraria usando SQL mínimo."""
    statement = sql_text(_COUNT_ROWS.format(table_name=table_name))
    return int(connection.execute(statement).scalar_one() or 0)


def _build_audit_snapshot(
    connection: Any,
    *,
    legacy_tables: dict[str, set[str]],
    target_counts_before: dict[str, int] | None = None,
) -> dict[str, TableAudit]:
    """Construye una foto comparativa source/target para tablas migrables."""
    audits: dict[str, TableAudit] = {}
    before = target_counts_before or {}
    for legacy_table_name in legacy_tables:
        target_table_name = _TABLE_NAME_MAP[legacy_table_name]
        source_count = _count_rows(connection, legacy_table_name)
        target_count_after = _count_rows(connection, target_table_name)
        missing_after = int(
            connection.execute(_MISSING_COUNT_BY_TABLE[legacy_table_name]).scalar_one()
            or 0
        )
        audits[legacy_table_name] = {
            "source_count": source_count,
            "target_count_before": int(before.get(legacy_table_name, 0)),
            "target_count_after": target_count_after,
            "missing_after": missing_after,
            "matched_after": source_count - missing_after,
        }
    return audits


def _existing_legacy_tables(factory: PostgresSessionFactory) -> dict[str, set[str]]:
    """Descubre tablas legacy presentes y las columnas expuestas por cada una."""
    with factory.get_connection() as connection:
        inspector = inspect(connection)
        tables: dict[str, set[str]] = {}
        for table_name in _LEGACY_REQUIRED_COLUMNS:
            if not inspector.has_table(table_name):
                continue
            tables[table_name] = {
                str(column["name"]).strip().lower()
                for column in inspector.get_columns(table_name)
            }
        return tables


def _build_missing_columns_error(
    table_name: str,
    *,
    missing_columns: set[str],
) -> RuntimeError:
    """Construye un error claro cuando la tabla legacy es incompatible."""
    missing_display = ", ".join(sorted(missing_columns))
    return RuntimeError(
        f"La tabla legacy '{table_name}' no es migrable porque faltan las "
        f"columnas requeridas: {missing_display}."
    )


def _validate_legacy_tables(legacy_tables: dict[str, set[str]]) -> None:
    """Verifica que cada tabla legacy presente tenga el contrato esperado."""
    for table_name, actual_columns in legacy_tables.items():
        missing_columns = _LEGACY_REQUIRED_COLUMNS[table_name] - actual_columns
        if missing_columns:
            raise _build_missing_columns_error(
                table_name,
                missing_columns=missing_columns,
            )


def run_legacy_postgres_data_migration(settings: object) -> dict[str, Any]:
    """Migra datos desde tablas legacy a las tablas actuales versionadas."""
    postgres_dsn = resolve_postgres_dsn(settings)
    if not postgres_dsn:
        raise ValueError(
            "POSTGRES_HOST y credenciales validas son obligatorios para "
            "migrar datos legacy de PostgreSQL."
        )

    schema_report = run_postgres_schema_command(
        settings,
        operation="upgrade",
        revision="head",
    )
    factory = PostgresSessionFactory.from_settings(settings)
    legacy_tables = _existing_legacy_tables(factory)
    _validate_legacy_tables(legacy_tables)

    migrated_jobs = 0
    migrated_repos = 0
    migrated_lexical_docs = 0
    fts_language = str(getattr(settings, "lexical_fts_language", "english") or "english")

    with factory.get_connection() as connection:
        target_counts_before = {
            legacy_table_name: _count_rows(
                connection,
                _TABLE_NAME_MAP[legacy_table_name],
            )
            for legacy_table_name in legacy_tables
        }
        if LEGACY_JOBS_TABLE_NAME in legacy_tables:
            migrated_jobs = int(
                connection.execute(_MIGRATE_LEGACY_JOBS).rowcount or 0
            )
        if LEGACY_REPOS_TABLE_NAME in legacy_tables:
            migrated_repos = int(
                connection.execute(_MIGRATE_LEGACY_REPOS).rowcount or 0
            )
        if LEGACY_LEXICAL_CORPUS_TABLE_NAME in legacy_tables:
            migrated_lexical_docs = int(
                connection.execute(
                    _MIGRATE_LEGACY_LEXICAL,
                    {"lang": fts_language},
                ).rowcount
                or 0
            )
        audit = _build_audit_snapshot(
            connection,
            legacy_tables=legacy_tables,
            target_counts_before=target_counts_before,
        )

    found_tables = sorted(legacy_tables)
    return {
        "enabled": True,
        "command": "migrate_legacy",
        "schema_action": schema_report.get("command", "upgrade"),
        "current_heads": list(schema_report.get("current_heads", [])),
        "expected_heads": list(schema_report.get("expected_heads", [])),
        "legacy_tables_found": found_tables,
        "legacy_tables_missing": [
            table_name
            for table_name in _LEGACY_REQUIRED_COLUMNS
            if table_name not in legacy_tables
        ],
        "jobs_migrated": migrated_jobs,
        "repos_migrated": migrated_repos,
        "lexical_docs_migrated": migrated_lexical_docs,
        "audit": audit,
    }