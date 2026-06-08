"""Bootstrap y validación del esquema PostgreSQL usando Alembic."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from coderag.core.settings import resolve_postgres_dsn
from coderag.storage.postgres_schema import (
    POSTGRES_JOBS_TABLE_NAME,
    POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
    POSTGRES_REPOS_TABLE_NAME,
)
from coderag.storage.postgres_session import (
    PostgresSessionFactory,
    to_sqlalchemy_postgres_url,
)


PostgresStartupPolicy = Literal["auto_upgrade", "validate"]
LegacySchemaState = Literal[
    "absent",
    "compatible",
    "upgradeable_missing_last_queried_at",
    "incompatible",
]

_BOOTSTRAP_CACHE: dict[tuple[str, PostgresStartupPolicy], dict[str, Any]] = {}
_REPO_LAST_QUERIED_AT_BASE_REVISION = "0002_drop_legacy_postgres_tables"
_DEFAULT_ALEMBIC_VERSION_TABLE = "alembic_version_repo"
_REQUIRED_COLUMNS_BY_TABLE: dict[str, set[str]] = {
    POSTGRES_JOBS_TABLE_NAME: {
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
    POSTGRES_REPOS_TABLE_NAME: {
        "id",
        "organization",
        "url",
        "branch",
        "local_path",
        "created_at",
        "updated_at",
        "last_queried_at",
        "embedding_provider",
        "embedding_model",
    },
    POSTGRES_LEXICAL_CORPUS_TABLE_NAME: {
        "id",
        "repo_id",
        "doc",
        "path",
        "symbol_name",
        "entity_type",
        "metadata",
        "fts_vector",
        "created_at",
    },
}


def _repo_root() -> Path:
    """Resuelve la raíz del repositorio a partir del layout src actual."""
    return Path(__file__).resolve().parents[3]


def _build_alembic_config(postgres_dsn: str) -> Config:
    """Construye la configuración Alembic enlazada al DSN efectivo."""
    config = Config(str(_repo_root() / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        to_sqlalchemy_postgres_url(postgres_dsn),
    )
    if not (config.get_main_option("version_table") or "").strip():
        config.set_main_option("version_table", _DEFAULT_ALEMBIC_VERSION_TABLE)
    return config


def _read_database_heads(factory: PostgresSessionFactory) -> set[str]:
    """Lee las revisiones aplicadas actualmente en la base activa."""
    with factory.get_connection() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"version_table": _DEFAULT_ALEMBIC_VERSION_TABLE},
        )
        return set(context.get_current_heads())


def _ensure_alembic_version_table_capacity(
    factory: PostgresSessionFactory,
    *,
    expected_heads: set[str],
    current_heads: set[str],
) -> bool:
    """Enscha version_num si la tabla de Alembic quedó con un VARCHAR corto."""
    required_length = max(
        [len(_REPO_LAST_QUERIED_AT_BASE_REVISION), 32]
        + [len(head) for head in expected_heads]
        + [len(head) for head in current_heads],
    )

    with factory.get_connection() as connection:
        inspector = inspect(connection)
        if not inspector.has_table(_DEFAULT_ALEMBIC_VERSION_TABLE):
            return False

        version_column = None
        for column in inspector.get_columns(_DEFAULT_ALEMBIC_VERSION_TABLE):
            if str(column.get("name") or "").strip().lower() == "version_num":
                version_column = column
                break

        if version_column is None:
            return False

        current_length = getattr(version_column.get("type"), "length", None)
        if current_length is None or int(current_length) >= required_length:
            return False

        quoted_table = connection.dialect.identifier_preparer.quote(
            _DEFAULT_ALEMBIC_VERSION_TABLE
        )
        connection.execute(
            text(
                f"ALTER TABLE {quoted_table} "
                "ALTER COLUMN version_num TYPE TEXT"
            )
        )
        return True


def _classify_legacy_schema(factory: PostgresSessionFactory) -> LegacySchemaState:
    """Clasifica si el esquema sin versionar es compatible, parcial o ausente."""
    with factory.get_connection() as connection:
        inspector = inspect(connection)
        existing_tables = {
            table_name
            for table_name in _REQUIRED_COLUMNS_BY_TABLE
            if inspector.has_table(table_name)
        }
        if not existing_tables:
            return "absent"

        required_tables = set(_REQUIRED_COLUMNS_BY_TABLE)
        if existing_tables != required_tables:
            return "incompatible"

        missing_columns_by_table: dict[str, set[str]] = {}
        for table_name, required_columns in _REQUIRED_COLUMNS_BY_TABLE.items():
            actual_columns = {
                str(column["name"]).strip().lower()
                for column in inspector.get_columns(table_name)
            }
            missing_columns_by_table[table_name] = required_columns - actual_columns

        if all(
            not missing_columns
            for missing_columns in missing_columns_by_table.values()
        ):
            return "compatible"

        repo_missing_columns = missing_columns_by_table[POSTGRES_REPOS_TABLE_NAME]
        if (
            repo_missing_columns == {"last_queried_at"}
            and not missing_columns_by_table[POSTGRES_JOBS_TABLE_NAME]
            and not missing_columns_by_table[
                POSTGRES_LEXICAL_CORPUS_TABLE_NAME
            ]
        ):
            return "upgradeable_missing_last_queried_at"

        return "incompatible"


def _resolve_startup_policy(settings: object) -> PostgresStartupPolicy:
    """Determina la política de startup a partir del entorno de ejecución."""
    resolver = getattr(settings, "resolve_postgres_startup_policy", None)
    if callable(resolver):
        return resolver()
    runtime_environment = str(
        getattr(settings, "runtime_environment", "development") or "development"
    ).strip().lower()
    return "validate" if runtime_environment == "production" else "auto_upgrade"


def _build_revision_error(
    *,
    current_heads: set[str],
    expected_heads: set[str],
    policy: PostgresStartupPolicy,
) -> RuntimeError:
    """Construye un error accionable cuando la base no está alineada."""
    current_display = sorted(current_heads) or ["<sin revision>"]
    expected_display = sorted(expected_heads) or ["<sin head>"]
    if policy == "validate":
        message = (
            "La base PostgreSQL no está alineada con las migraciones esperadas. "
            f"Revision actual: {current_display}. Heads esperados: {expected_display}. "
            "En producción debes ejecutar Alembic fuera del proceso antes de iniciar "
            "la API o el worker."
        )
    else:
        message = (
            "No se pudo dejar PostgreSQL alineado con las migraciones esperadas. "
            f"Revision actual: {current_display}. Heads esperados: {expected_display}."
        )
    return RuntimeError(message)


def _build_incompatible_legacy_schema_error() -> RuntimeError:
    """Construye un error accionable para esquemas legacy parciales."""
    return RuntimeError(
        "La base PostgreSQL contiene tablas legacy parciales o incompatibles y "
        "no se puede estampar automáticamente en Alembic. Completa una migración "
        "manual o recrea el esquema antes de iniciar el runtime."
    )


def ensure_postgres_schema_ready(
    settings: object,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Prepara o valida el esquema PostgreSQL según la política del entorno."""
    postgres_dsn = resolve_postgres_dsn(settings)
    policy = _resolve_startup_policy(settings)

    if not postgres_dsn:
        return {
            "enabled": False,
            "policy": policy,
            "action": "skipped",
            "current_heads": [],
            "expected_heads": [],
            "cached": False,
        }

    cache_key = (postgres_dsn, policy)
    if not force:
        cached = _BOOTSTRAP_CACHE.get(cache_key)
        if cached is not None:
            report = dict(cached)
            report["cached"] = True
            return report

    factory = PostgresSessionFactory.from_settings(settings)
    config = _build_alembic_config(postgres_dsn)
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())
    current_heads = _read_database_heads(factory)
    _ensure_alembic_version_table_capacity(
        factory,
        expected_heads=expected_heads,
        current_heads=current_heads,
    )
    action = "validated"

    if policy == "auto_upgrade":
        legacy_state = _classify_legacy_schema(factory)
        if not current_heads and legacy_state == "compatible":
            command.stamp(config, "head")
            action = "stamped_legacy_schema"
        elif (
            not current_heads
            and legacy_state == "upgradeable_missing_last_queried_at"
        ):
            command.stamp(config, _REPO_LAST_QUERIED_AT_BASE_REVISION)
            command.upgrade(config, "head")
            action = "upgraded_unversioned_schema"
        elif not current_heads and legacy_state == "incompatible":
            raise _build_incompatible_legacy_schema_error()
        elif current_heads != expected_heads:
            command.upgrade(config, "head")
            action = "upgraded"
        else:
            action = "already_current"

        current_heads = _read_database_heads(factory)
        if current_heads != expected_heads:
            raise _build_revision_error(
                current_heads=current_heads,
                expected_heads=expected_heads,
                policy=policy,
            )
    elif current_heads != expected_heads:
        raise _build_revision_error(
            current_heads=current_heads,
            expected_heads=expected_heads,
            policy=policy,
        )

    report = {
        "enabled": True,
        "policy": policy,
        "action": action,
        "current_heads": sorted(current_heads),
        "expected_heads": sorted(expected_heads),
        "cached": False,
    }
    _BOOTSTRAP_CACHE[cache_key] = dict(report)
    return report