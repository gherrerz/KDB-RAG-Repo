"""Contract tests to prevent Alembic version-table regressions."""

from __future__ import annotations

import re
from pathlib import Path

from conftest import build_test_postgres_dsn
from coderag.storage import postgres_schema_admin, postgres_startup

_REPO_VERSION_TABLE = "alembic_version_repo"
_CRITICAL_FILES = (
    Path("alembic.ini"),
    Path("migrations/env.py"),
    Path("src/coderag/storage/postgres_startup.py"),
    Path("src/coderag/storage/postgres_schema_admin.py"),
)


def test_critical_files_do_not_use_legacy_alembic_version_literal() -> None:
    """Critical migration paths must never fallback to alembic_version."""
    legacy_pattern = re.compile(r"version_table\s*=\s*alembic_version(\s|$)")

    for file_path in _CRITICAL_FILES:
        content = file_path.read_text(encoding="utf-8")
        assert '"alembic_version"' not in content
        assert "'alembic_version'" not in content
        assert legacy_pattern.search(content) is None


def test_repo_version_table_is_explicit_in_all_critical_layers() -> None:
    """Config, migration env and runtime/admin helpers must pin repo table."""
    alembic_ini = Path("alembic.ini").read_text(encoding="utf-8")
    migration_env = Path("migrations/env.py").read_text(encoding="utf-8")
    startup_module = Path("src/coderag/storage/postgres_startup.py").read_text(
        encoding="utf-8"
    )
    admin_module = Path("src/coderag/storage/postgres_schema_admin.py").read_text(
        encoding="utf-8"
    )

    assert "version_table = alembic_version_repo" in alembic_ini
    assert 'return "alembic_version_repo"' in migration_env
    assert (
        '_DEFAULT_ALEMBIC_VERSION_TABLE = "alembic_version_repo"'
        in startup_module
    )
    assert '_DEFAULT_ALEMBIC_VERSION_TABLE = "alembic_version_repo"' in admin_module


def test_bootstrap_helpers_pin_repo_version_table() -> None:
    """Both bootstrap helpers should always resolve the repo version table."""
    dsn = build_test_postgres_dsn(POSTGRES_DB="coipo_db")

    startup_config = postgres_startup._build_alembic_config(dsn)
    admin_config = postgres_schema_admin._build_alembic_config(dsn)

    assert startup_config.get_main_option("version_table") == _REPO_VERSION_TABLE
    assert admin_config.get_main_option("version_table") == _REPO_VERSION_TABLE