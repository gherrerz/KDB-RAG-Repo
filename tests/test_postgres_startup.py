"""Pruebas para el bootstrap mixto de migraciones PostgreSQL."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from conftest import build_test_postgres_dsn

from coderag.storage import postgres_startup


def _settings(
    *,
    runtime_environment: str = "development",
    dsn: str = build_test_postgres_dsn(POSTGRES_DB="db"),
) -> SimpleNamespace:
    """Construye settings mínimos para el bootstrap de Postgres."""
    return SimpleNamespace(
        runtime_environment=runtime_environment,
        resolve_postgres_dsn=lambda: dsn,
        resolve_postgres_startup_policy=lambda: (
            "validate" if runtime_environment == "production" else "auto_upgrade"
        ),
        postgres_pool_size=5,
        postgres_pool_timeout=30.0,
    )


def test_skips_when_postgres_is_not_configured() -> None:
    """Sin DSN de Postgres el bootstrap debe ser un no-op explícito."""
    result = postgres_startup.ensure_postgres_schema_ready(
        _settings(dsn=""),
        force=True,
    )

    assert result["enabled"] is False
    assert result["action"] == "skipped"


def test_development_upgrades_when_database_is_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En desarrollo se deben aplicar migraciones pendientes automáticamente."""
    settings = _settings(runtime_environment="development")
    factory = MagicMock()
    monkeypatch.setattr(
        postgres_startup.PostgresSessionFactory,
        "from_settings",
        lambda value: factory,
    )
    monkeypatch.setattr(
        postgres_startup,
        "_read_database_heads",
        MagicMock(side_effect=[set(), {"0001_initial_postgres_schema"}]),
    )
    monkeypatch.setattr(
        postgres_startup,
        "_classify_legacy_schema",
        lambda value: "absent",
    )
    monkeypatch.setattr(
        postgres_startup.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(get_heads=lambda: ["0001_initial_postgres_schema"]),
    )
    capacity_mock = MagicMock(return_value=False)
    monkeypatch.setattr(
        postgres_startup,
        "_ensure_alembic_version_table_capacity",
        capacity_mock,
    )
    upgrade_mock = MagicMock()
    monkeypatch.setattr(postgres_startup.command, "upgrade", upgrade_mock)
    monkeypatch.setattr(postgres_startup.command, "stamp", MagicMock())

    result = postgres_startup.ensure_postgres_schema_ready(settings, force=True)

    assert result["policy"] == "auto_upgrade"
    assert result["action"] == "upgraded"
    capacity_mock.assert_called_once_with(
        factory,
        expected_heads={"0001_initial_postgres_schema"},
        current_heads=set(),
    )
    upgrade_mock.assert_called_once()


def test_development_stamps_legacy_schema_without_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En desarrollo, una base legacy compatible se versiona con stamp head."""
    settings = _settings(runtime_environment="development")
    factory = MagicMock()
    monkeypatch.setattr(
        postgres_startup.PostgresSessionFactory,
        "from_settings",
        lambda value: factory,
    )
    monkeypatch.setattr(
        postgres_startup,
        "_read_database_heads",
        MagicMock(side_effect=[set(), {"0001_initial_postgres_schema"}]),
    )
    monkeypatch.setattr(
        postgres_startup,
        "_classify_legacy_schema",
        lambda value: "compatible",
    )
    monkeypatch.setattr(
        postgres_startup.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(get_heads=lambda: ["0001_initial_postgres_schema"]),
    )
    upgrade_mock = MagicMock()
    stamp_mock = MagicMock()
    monkeypatch.setattr(postgres_startup.command, "upgrade", upgrade_mock)
    monkeypatch.setattr(postgres_startup.command, "stamp", stamp_mock)

    result = postgres_startup.ensure_postgres_schema_ready(settings, force=True)

    assert result["action"] == "stamped_legacy_schema"
    upgrade_mock.assert_not_called()
    stamp_mock.assert_called_once()


def test_development_upgrades_unversioned_schema_missing_last_queried_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una base sin versionar equivalente a 0002 debe avanzar a head."""
    settings = _settings(runtime_environment="development")
    factory = MagicMock()
    monkeypatch.setattr(
        postgres_startup.PostgresSessionFactory,
        "from_settings",
        lambda value: factory,
    )
    monkeypatch.setattr(
        postgres_startup,
        "_read_database_heads",
        MagicMock(
            side_effect=[set(), {"0003_add_repo_last_queried_at"}]
        ),
    )
    monkeypatch.setattr(
        postgres_startup,
        "_classify_legacy_schema",
        lambda value: "upgradeable_missing_last_queried_at",
    )
    monkeypatch.setattr(
        postgres_startup.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(
            get_heads=lambda: ["0003_add_repo_last_queried_at"]
        ),
    )
    upgrade_mock = MagicMock()
    stamp_mock = MagicMock()
    monkeypatch.setattr(postgres_startup.command, "upgrade", upgrade_mock)
    monkeypatch.setattr(postgres_startup.command, "stamp", stamp_mock)

    result = postgres_startup.ensure_postgres_schema_ready(settings, force=True)

    assert result["action"] == "upgraded_unversioned_schema"
    stamp_mock.assert_called_once_with(
        ANY,
        "0002_drop_legacy_postgres_tables",
    )
    upgrade_mock.assert_called_once_with(ANY, "head")


def test_development_rejects_incompatible_legacy_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una base parcial legacy no debe quedar estampada como si fuera válida."""
    settings = _settings(runtime_environment="development")
    factory = MagicMock()
    monkeypatch.setattr(
        postgres_startup.PostgresSessionFactory,
        "from_settings",
        lambda value: factory,
    )
    monkeypatch.setattr(postgres_startup, "_read_database_heads", lambda value: set())
    monkeypatch.setattr(
        postgres_startup,
        "_classify_legacy_schema",
        lambda value: "incompatible",
    )
    monkeypatch.setattr(
        postgres_startup.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(get_heads=lambda: ["0001_initial_postgres_schema"]),
    )
    monkeypatch.setattr(postgres_startup.command, "upgrade", MagicMock())
    monkeypatch.setattr(postgres_startup.command, "stamp", MagicMock())

    with pytest.raises(RuntimeError) as exc_info:
        postgres_startup.ensure_postgres_schema_ready(settings, force=True)

    assert "legacy parciales o incompatibles" in str(exc_info.value)


def test_production_validate_fails_when_database_is_outdated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En producción no se deben aplicar cambios, solo validar heads."""
    settings = _settings(runtime_environment="production")
    factory = MagicMock()
    monkeypatch.setattr(
        postgres_startup.PostgresSessionFactory,
        "from_settings",
        lambda value: factory,
    )
    monkeypatch.setattr(postgres_startup, "_read_database_heads", lambda value: set())
    monkeypatch.setattr(
        postgres_startup.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(get_heads=lambda: ["0001_initial_postgres_schema"]),
    )
    monkeypatch.setattr(postgres_startup.command, "upgrade", MagicMock())

    with pytest.raises(RuntimeError) as exc_info:
        postgres_startup.ensure_postgres_schema_ready(settings, force=True)

    assert "En producción debes ejecutar Alembic" in str(exc_info.value)


def test_production_validate_succeeds_when_heads_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En producción el bootstrap debe aceptar una base ya migrada."""
    settings = _settings(runtime_environment="production")
    factory = MagicMock()
    monkeypatch.setattr(
        postgres_startup.PostgresSessionFactory,
        "from_settings",
        lambda value: factory,
    )
    monkeypatch.setattr(
        postgres_startup,
        "_read_database_heads",
        lambda value: {"0001_initial_postgres_schema"},
    )
    monkeypatch.setattr(
        postgres_startup.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(get_heads=lambda: ["0001_initial_postgres_schema"]),
    )

    result = postgres_startup.ensure_postgres_schema_ready(settings, force=True)

    assert result["policy"] == "validate"
    assert result["action"] == "validated"


def test_read_database_heads_uses_repo_version_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La lectura de heads debe usar la tabla Alembic aislada del repo."""
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
    monkeypatch.setattr(postgres_startup.MigrationContext, "configure", _fake_configure)

    heads = postgres_startup._read_database_heads(factory)

    assert heads == {"0001_initial_postgres_schema"}
    assert captured["opts"] == {"version_table": "alembic_version_repo"}


def test_ensure_alembic_version_table_capacity_alters_short_version_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensancha alembic_version_repo.version_num cuando el ancho queda corto."""

    class _FakeConnection:
        def __init__(self) -> None:
            self.executed: list[object] = []
            self.dialect = SimpleNamespace(
                identifier_preparer=SimpleNamespace(quote=lambda value: value)
            )

        def execute(self, statement: object) -> None:
            self.executed.append(statement)

    class _ConnectionContext:
        def __init__(self, connection: _FakeConnection) -> None:
            self._connection = connection

        def __enter__(self) -> _FakeConnection:
            return self._connection

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> bool:
            return False

    fake_connection = _FakeConnection()
    factory = SimpleNamespace(
        get_connection=lambda: _ConnectionContext(fake_connection)
    )

    class _FakeInspector:
        def has_table(self, table_name: str) -> bool:
            return table_name == "alembic_version_repo"

        def get_columns(self, table_name: str) -> list[dict[str, object]]:
            assert table_name == "alembic_version_repo"
            return [
                {
                    "name": "version_num",
                    "type": SimpleNamespace(length=32),
                }
            ]

    monkeypatch.setattr(postgres_startup, "inspect", lambda conn: _FakeInspector())

    changed = postgres_startup._ensure_alembic_version_table_capacity(
        factory,
        expected_heads={"0004_add_ingestion_snapshots_table"},
        current_heads={"0003_add_repo_last_queried_at"},
    )

    assert changed is True
    assert len(fake_connection.executed) == 1
    assert str(fake_connection.executed[0]) == (
        "ALTER TABLE alembic_version_repo ALTER COLUMN version_num TYPE TEXT"
    )


def test_ensure_alembic_version_table_capacity_skips_when_column_is_wide_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No altera la tabla de Alembic si version_num ya soporta el head actual."""

    class _FakeConnection:
        def __init__(self) -> None:
            self.executed: list[object] = []
            self.dialect = SimpleNamespace(
                identifier_preparer=SimpleNamespace(quote=lambda value: value)
            )

        def execute(self, statement: object) -> None:
            self.executed.append(statement)

    class _ConnectionContext:
        def __init__(self, connection: _FakeConnection) -> None:
            self._connection = connection

        def __enter__(self) -> _FakeConnection:
            return self._connection

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> bool:
            return False

    fake_connection = _FakeConnection()
    factory = SimpleNamespace(
        get_connection=lambda: _ConnectionContext(fake_connection)
    )

    class _FakeInspector:
        def has_table(self, table_name: str) -> bool:
            return table_name == "alembic_version_repo"

        def get_columns(self, table_name: str) -> list[dict[str, object]]:
            assert table_name == "alembic_version_repo"
            return [
                {
                    "name": "version_num",
                    "type": SimpleNamespace(length=128),
                }
            ]

    monkeypatch.setattr(postgres_startup, "inspect", lambda conn: _FakeInspector())

    changed = postgres_startup._ensure_alembic_version_table_capacity(
        factory,
        expected_heads={"0004_add_ingestion_snapshots_table"},
        current_heads={"0003_add_repo_last_queried_at"},
    )

    assert changed is False
    assert fake_connection.executed == []