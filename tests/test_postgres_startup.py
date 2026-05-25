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
    upgrade_mock = MagicMock()
    monkeypatch.setattr(postgres_startup.command, "upgrade", upgrade_mock)
    monkeypatch.setattr(postgres_startup.command, "stamp", MagicMock())

    result = postgres_startup.ensure_postgres_schema_ready(settings, force=True)

    assert result["policy"] == "auto_upgrade"
    assert result["action"] == "upgraded"
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