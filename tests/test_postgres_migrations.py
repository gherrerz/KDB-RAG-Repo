"""Pruebas básicas para el wiring de migraciones PostgreSQL."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_initial_migration_module_exports_expected_contract() -> None:
    """La revisión inicial debe exponer el contrato mínimo de Alembic."""
    migration_path = Path("migrations/versions/0001_initial_postgres_schema.py")
    spec = importlib.util.spec_from_file_location(
        "migration_0001_initial_postgres_schema",
        migration_path,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0001_initial_postgres_schema"
    assert module.down_revision is None
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_alembic_ini_exists_with_src_prepend() -> None:
    """La configuración de Alembic debe resolver el layout src del repo."""
    content = Path("alembic.ini").read_text(encoding="utf-8")

    assert "script_location = migrations" in content
    assert "prepend_sys_path = src" in content
    assert "sqlalchemy.url =\n" in content
    assert "sqlalchemy.url = postgresql://" not in content


def test_alembic_env_prefers_configured_sqlalchemy_url() -> None:
    """El bootstrap debe poder inyectar la URL sin depender del env global."""
    content = Path("migrations/env.py").read_text(encoding="utf-8")

    assert 'config.get_main_option("sqlalchemy.url")' in content
    assert "_has_explicit_sqlalchemy_url" in content
    assert '"postgresql://"' in content


def test_startup_bootstrap_module_exists() -> None:
    """El bootstrap de Postgres debe existir como capa separada del storage."""
    bootstrap_path = Path("src/coderag/storage/postgres_startup.py")

    assert bootstrap_path.exists()