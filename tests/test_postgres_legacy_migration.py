"""Pruebas para la migración de tablas PostgreSQL legacy."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import build_test_postgres_dsn

from coderag.storage import postgres_legacy_migration


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        resolve_postgres_dsn=lambda: build_test_postgres_dsn(POSTGRES_DB="db"),
        postgres_pool_size=5,
        postgres_pool_timeout=30.0,
        lexical_fts_language="english",
    )


class _FakeResult:
    def __init__(self, rowcount: int, scalar_value: int | None = None) -> None:
        self.rowcount = rowcount
        self._scalar_value = rowcount if scalar_value is None else scalar_value

    def scalar_one(self) -> int:
        return self._scalar_value


class _FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self._counts = {
            "jobs": 2,
            "repos": 3,
            "lexical_corpus": 5,
            "tbl_repository_jobs": 0,
            "tbl_repository_repos": 0,
            "tbl_repository_lexicalcorpus": 0,
        }

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if sql.startswith("SELECT count(*) FROM "):
            table_name = sql.removeprefix("SELECT count(*) FROM ").strip()
            return _FakeResult(0, scalar_value=self._counts.get(table_name, 0))
        if "FROM jobs AS legacy" in sql and "LEFT JOIN tbl_repository_jobs" in sql:
            return _FakeResult(0, scalar_value=0)
        if "FROM repos AS legacy" in sql and "LEFT JOIN tbl_repository_repos" in sql:
            return _FakeResult(0, scalar_value=0)
        if (
            "FROM lexical_corpus AS legacy" in sql
            and "LEFT JOIN tbl_repository_lexicalcorpus" in sql
        ):
            return _FakeResult(0, scalar_value=0)
        if "INSERT INTO tbl_repository_jobs" in sql:
            self._counts["tbl_repository_jobs"] = self._counts["jobs"]
            return _FakeResult(2)
        if "INSERT INTO tbl_repository_repos" in sql:
            self._counts["tbl_repository_repos"] = self._counts["repos"]
            return _FakeResult(3)
        if "INSERT INTO tbl_repository_lexicalcorpus" in sql:
            self._counts["tbl_repository_lexicalcorpus"] = self._counts[
                "lexical_corpus"
            ]
            return _FakeResult(5)
        return _FakeResult(0)


class _FakeFactory:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    @contextmanager
    def get_connection(self) -> Iterator[_FakeConnection]:
        yield self._connection


class _FakeInspector:
    def __init__(self, tables: dict[str, set[str]]) -> None:
        self._tables = tables

    def has_table(self, table_name: str) -> bool:
        return table_name in self._tables

    def get_columns(self, table_name: str):
        return [{"name": column_name} for column_name in self._tables[table_name]]


def test_returns_zero_counts_when_no_legacy_tables(monkeypatch) -> None:
    """Si no existen tablas legacy, el reporte debe quedar en cero."""
    connection = _FakeConnection()
    settings = _settings()
    monkeypatch.setattr(
        postgres_legacy_migration,
        "run_postgres_schema_command",
        lambda settings, operation, revision: {
            "command": "upgrade",
            "current_heads": ["0001_initial_postgres_schema"],
            "expected_heads": ["0001_initial_postgres_schema"],
        },
    )
    monkeypatch.setattr(
        postgres_legacy_migration.PostgresSessionFactory,
        "from_settings",
        lambda settings: _FakeFactory(connection),
    )
    monkeypatch.setattr(
        postgres_legacy_migration,
        "inspect",
        lambda value: _FakeInspector({}),
    )

    result = postgres_legacy_migration.run_legacy_postgres_data_migration(
        settings
    )

    assert result == {
        "enabled": True,
        "command": "migrate_legacy",
        "schema_action": "upgrade",
        "current_heads": ["0001_initial_postgres_schema"],
        "expected_heads": ["0001_initial_postgres_schema"],
        "legacy_tables_found": [],
        "legacy_tables_missing": ["jobs", "repos", "lexical_corpus"],
        "jobs_migrated": 0,
        "repos_migrated": 0,
        "lexical_docs_migrated": 0,
        "audit": {},
    }
    assert connection.calls == []


def test_rejects_legacy_table_with_missing_columns(monkeypatch) -> None:
    """Una tabla legacy parcial no debe migrarse silenciosamente."""
    connection = _FakeConnection()
    settings = _settings()
    monkeypatch.setattr(
        postgres_legacy_migration,
        "run_postgres_schema_command",
        lambda settings, operation, revision: {
            "command": "upgrade",
            "current_heads": ["0001_initial_postgres_schema"],
            "expected_heads": ["0001_initial_postgres_schema"],
        },
    )
    monkeypatch.setattr(
        postgres_legacy_migration.PostgresSessionFactory,
        "from_settings",
        lambda settings: _FakeFactory(connection),
    )
    monkeypatch.setattr(
        postgres_legacy_migration,
        "inspect",
        lambda value: _FakeInspector({"jobs": {"id", "status"}}),
    )

    with pytest.raises(RuntimeError) as exc_info:
        postgres_legacy_migration.run_legacy_postgres_data_migration(settings)

    assert "tabla legacy 'jobs'" in str(exc_info.value)
    assert "created_at" in str(exc_info.value)


def test_migrates_all_legacy_tables_and_uses_fts_language(monkeypatch) -> None:
    """La migración debe copiar jobs, repos y lexical con el lenguaje FTS."""
    connection = _FakeConnection()
    settings = _settings()
    schema_admin_mock = MagicMock(
        return_value={
            "command": "upgrade",
            "current_heads": ["0001_initial_postgres_schema"],
            "expected_heads": ["0001_initial_postgres_schema"],
        }
    )
    monkeypatch.setattr(
        postgres_legacy_migration,
        "run_postgres_schema_command",
        schema_admin_mock,
    )
    monkeypatch.setattr(
        postgres_legacy_migration.PostgresSessionFactory,
        "from_settings",
        lambda settings: _FakeFactory(connection),
    )
    monkeypatch.setattr(
        postgres_legacy_migration,
        "inspect",
        lambda value: _FakeInspector(
            postgres_legacy_migration._LEGACY_REQUIRED_COLUMNS
        ),
    )

    result = postgres_legacy_migration.run_legacy_postgres_data_migration(
        settings
    )

    assert result == {
        "enabled": True,
        "command": "migrate_legacy",
        "schema_action": "upgrade",
        "current_heads": ["0001_initial_postgres_schema"],
        "expected_heads": ["0001_initial_postgres_schema"],
        "legacy_tables_found": ["jobs", "lexical_corpus", "repos"],
        "legacy_tables_missing": [],
        "jobs_migrated": 2,
        "repos_migrated": 3,
        "lexical_docs_migrated": 5,
        "audit": {
            "jobs": {
                "source_count": 2,
                "target_count_before": 0,
                "target_count_after": 2,
                "missing_after": 0,
                "matched_after": 2,
            },
            "repos": {
                "source_count": 3,
                "target_count_before": 0,
                "target_count_after": 3,
                "missing_after": 0,
                "matched_after": 3,
            },
            "lexical_corpus": {
                "source_count": 5,
                "target_count_before": 0,
                "target_count_after": 5,
                "missing_after": 0,
                "matched_after": 5,
            },
        },
    }
    schema_admin_mock.assert_called_once_with(
        settings,
        operation="upgrade",
        revision="head",
    )
    assert any(params == {"lang": "english"} for _, params in connection.calls)