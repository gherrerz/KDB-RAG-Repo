"""Pruebas del wrapper administrativo de esquema PostgreSQL."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from conftest import build_test_postgres_dsn

from coderag.storage import postgres_schema_admin


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        resolve_postgres_dsn=lambda: build_test_postgres_dsn(POSTGRES_DB="db"),
        postgres_pool_size=5,
        postgres_pool_timeout=30.0,
    )


def test_current_reports_applied_and_expected_heads(
    monkeypatch,
) -> None:
    """current debe exponer heads aplicados y esperados sin mutar la base."""
    monkeypatch.setattr(
        postgres_schema_admin.PostgresSessionFactory,
        "from_settings",
        lambda settings: MagicMock(),
    )
    monkeypatch.setattr(
        postgres_schema_admin,
        "_read_database_heads",
        lambda factory: {"0001_initial_postgres_schema"},
    )
    monkeypatch.setattr(
        postgres_schema_admin.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(
            get_heads=lambda: ["0001_initial_postgres_schema"]
        ),
    )

    result = postgres_schema_admin.run_postgres_schema_command(
        _settings(),
        operation="current",
    )

    assert result == {
        "enabled": True,
        "command": "current",
        "current_heads": ["0001_initial_postgres_schema"],
        "expected_heads": ["0001_initial_postgres_schema"],
    }


def test_upgrade_runs_alembic_upgrade_and_reports_revision(
    monkeypatch,
) -> None:
    """upgrade debe ejecutar Alembic y devolver la revision objetivo."""
    upgrade_mock = MagicMock()
    monkeypatch.setattr(postgres_schema_admin.command, "upgrade", upgrade_mock)
    monkeypatch.setattr(
        postgres_schema_admin.PostgresSessionFactory,
        "from_settings",
        lambda settings: MagicMock(),
    )
    monkeypatch.setattr(
        postgres_schema_admin,
        "_read_database_heads",
        lambda factory: {"0001_initial_postgres_schema"},
    )
    monkeypatch.setattr(
        postgres_schema_admin.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(
            get_heads=lambda: ["0001_initial_postgres_schema"]
        ),
    )

    result = postgres_schema_admin.run_postgres_schema_command(
        _settings(),
        operation="upgrade",
        revision="head",
    )

    assert result == {
        "enabled": True,
        "command": "upgrade",
        "current_heads": ["0001_initial_postgres_schema"],
        "expected_heads": ["0001_initial_postgres_schema"],
        "revision": "head",
    }
    upgrade_mock.assert_called_once()


def test_validate_reuses_startup_validation_policy(monkeypatch) -> None:
    """validate debe forzar la misma politica segura de produccion."""
    captured: dict[str, object] = {}

    def _fake_ensure(settings: object, *, force: bool) -> dict[str, object]:
        captured["policy"] = settings.resolve_postgres_startup_policy()
        captured["dsn"] = settings.resolve_postgres_dsn()
        captured["force"] = force
        return {
            "enabled": True,
            "policy": "validate",
            "action": "validated",
            "current_heads": ["0001_initial_postgres_schema"],
            "expected_heads": ["0001_initial_postgres_schema"],
            "cached": False,
        }

    monkeypatch.setattr(
        postgres_schema_admin,
        "ensure_postgres_schema_ready",
        _fake_ensure,
    )

    result = postgres_schema_admin.run_postgres_schema_command(
        _settings(),
        operation="validate",
    )

    assert captured == {
        "policy": "validate",
        "dsn": build_test_postgres_dsn(POSTGRES_DB="db"),
        "force": True,
    }
    assert result == {
        "enabled": True,
        "policy": "validate",
        "action": "validated",
        "current_heads": ["0001_initial_postgres_schema"],
        "expected_heads": ["0001_initial_postgres_schema"],
        "cached": False,
        "command": "validate",
    }


def test_read_database_heads_uses_repo_version_table(monkeypatch) -> None:
    """Schema admin debe leer heads desde la tabla Alembic del repo."""
    captured: dict[str, object] = {}

    class _FakeMigrationContext:
        def get_current_heads(self) -> list[str]:
            return ["0001_initial_postgres_schema"]

    def _fake_configure(
        connection: object,
        opts: dict[str, object] | None = None,
    ) -> _FakeMigrationContext:
        captured["opts"] = opts or {}
        return _FakeMigrationContext()

    class _ConnectionContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> bool:
            return False

    factory = SimpleNamespace(get_connection=lambda: _ConnectionContext())

    import alembic.runtime.migration as migration_module

    monkeypatch.setattr(migration_module.MigrationContext, "configure", _fake_configure)

    heads = postgres_schema_admin._read_database_heads(factory)

    assert heads == {"0001_initial_postgres_schema"}
    assert captured["opts"] == {"version_table": "alembic_version_repo"}