"""Entorno Alembic para versionar el esquema PostgreSQL operativo."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.storage.postgres_schema import POSTGRES_SCHEMA_METADATA
from coderag.storage.postgres_session import to_sqlalchemy_postgres_url


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = POSTGRES_SCHEMA_METADATA


def _get_alembic_version_table() -> str:
    """Resuelve la tabla de versionado Alembic usada por este proyecto."""
    configured_table = (config.get_main_option("version_table") or "").strip()
    if configured_table:
        return configured_table
    return "alembic_version_repo"


def _has_explicit_sqlalchemy_url(configured_url: str) -> bool:
    """Indica si la URL configurada apunta a un destino utilizable."""
    normalized = configured_url.strip()
    if not normalized:
        return False

    return normalized not in {
        "postgres://",
        "postgresql://",
        "postgresql+psycopg://",
    }


def _get_postgres_url() -> str:
    """Resuelve la URL de Postgres para ejecutar migraciones."""
    configured_url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if _has_explicit_sqlalchemy_url(configured_url):
        return configured_url

    settings = get_settings()
    postgres_url = resolve_postgres_dsn(settings)
    if not postgres_url:
        raise RuntimeError(
            "No se pudo resolver la DSN de Postgres para Alembic. "
            "Configura POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER y "
            "POSTGRES_PASSWORD antes de migrar."
        )
    return to_sqlalchemy_postgres_url(postgres_url)


def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo offline generando SQL."""
    context.configure(
        url=_get_postgres_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table=_get_alembic_version_table(),
    )

    with context.begin_transaction():
        context.run_migrations()


def _resolve_migration_timeouts() -> tuple[int, int]:
    """Resuelve lock_timeout y statement_timeout (ms) para la migración."""
    settings = get_settings()
    lock_timeout_ms = int(
        getattr(settings, "postgres_migration_lock_timeout_ms", 15000)
    )
    statement_timeout_ms = int(
        getattr(settings, "postgres_migration_statement_timeout_ms", 300000)
    )
    return lock_timeout_ms, statement_timeout_ms


# Clave estable para serializar migradores concurrentes vía advisory lock.
_ADVISORY_LOCK_KEY = "coderag_alembic_repo"


def run_migrations_online() -> None:
    """Ejecuta migraciones en modo online sobre la base configurada."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_postgres_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    lock_timeout_ms, statement_timeout_ms = _resolve_migration_timeouts()

    with connectable.connect() as connection:
        # Acota la espera de locks/sentencias para que un bloqueo (p.ej. un
        # backend huérfano de un pod anterior) falle rápido en vez de colgar el
        # arranque de forma indefinida. Se fija antes de pedir el advisory lock
        # para que la propia espera del advisory lock también quede acotada.
        connection.exec_driver_sql(f"SET lock_timeout = '{lock_timeout_ms}'")
        connection.exec_driver_sql(
            f"SET statement_timeout = '{statement_timeout_ms}'"
        )

        # Serializa migradores concurrentes (p.ej. rollout con surge): uno espera
        # de forma acotada en vez de provocar un bloqueo cruzado entre pods.
        connection.exec_driver_sql(
            f"SELECT pg_advisory_lock(hashtext('{_ADVISORY_LOCK_KEY}'))"
        )
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                version_table=_get_alembic_version_table(),
            )

            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.exec_driver_sql(
                f"SELECT pg_advisory_unlock(hashtext('{_ADVISORY_LOCK_KEY}'))"
            )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()