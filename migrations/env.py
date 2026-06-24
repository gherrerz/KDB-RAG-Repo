"""Entorno Alembic para versionar el esquema PostgreSQL operativo."""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, Engine, engine_from_config, pool, text

from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.storage.postgres_schema import POSTGRES_SCHEMA_METADATA
from coderag.storage.postgres_session import (
    _describe_postgres_target,
    build_postgres_connect_args,
    to_sqlalchemy_postgres_url,
)


config = context.config

logger = logging.getLogger("alembic.env")

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


# Clave estable para serializar migradores concurrentes vía advisory lock.
_ADVISORY_LOCK_KEY = "coderag_alembic_repo"


def _migration_server_settings() -> dict[str, str]:
    """GUCs de Postgres aplicados a la conexión de migración (en ms)."""
    settings = get_settings()
    lock_timeout_ms = int(
        getattr(settings, "postgres_migration_lock_timeout_ms", 15000)
    )
    statement_timeout_ms = int(
        getattr(settings, "postgres_migration_statement_timeout_ms", 300000)
    )
    idle_tx_timeout_ms = int(
        getattr(settings, "postgres_migration_idle_tx_timeout_ms", 60000)
    )
    return {
        "lock_timeout": str(lock_timeout_ms),
        "statement_timeout": str(statement_timeout_ms),
        # Garantiza que un backend huérfano (pod muerto a mitad de migración)
        # sea segado por Postgres, liberando locks para el siguiente arranque.
        "idle_in_transaction_session_timeout": str(idle_tx_timeout_ms),
    }


def _log_effective_gucs(connection: Connection, target: str) -> None:
    """Loguea destino y GUCs efectivos: confirma código vivo y estado real."""
    try:
        lock_timeout = connection.exec_driver_sql("SHOW lock_timeout").scalar()
        statement_timeout = connection.exec_driver_sql(
            "SHOW statement_timeout"
        ).scalar()
        idle_tx_timeout = connection.exec_driver_sql(
            "SHOW idle_in_transaction_session_timeout"
        ).scalar()
        logger.info(
            "Migración Postgres -> %s | lock_timeout=%s statement_timeout=%s "
            "idle_in_transaction_session_timeout=%s",
            target,
            lock_timeout,
            statement_timeout,
            idle_tx_timeout,
        )
    except Exception:  # pragma: no cover - diagnóstico best-effort
        logger.warning("No se pudieron leer los GUCs efectivos de migración.")


def _log_blocking_sessions(engine: Engine) -> None:
    """Loguea sesiones que podrían estar bloqueando la migración."""
    query = text(
        "SELECT pid, state, wait_event_type, left(query, 200) AS query "
        "FROM pg_stat_activity "
        "WHERE datname = current_database() "
        "AND pid <> pg_backend_pid() "
        "AND (state = 'idle in transaction' "
        "OR query ILIKE '%tbl_repository_ingestionsnapshots%')"
    )
    try:
        # Conexión separada: la de migración puede estar en transacción abortada.
        with engine.connect() as diag:
            rows = diag.execute(query).fetchall()
        if not rows:
            logger.error(
                "Migración bloqueada/fallida pero no se detectaron sesiones "
                "bloqueadoras evidentes (posible corte de red/mesh)."
            )
            return
        for row in rows:
            logger.error(
                "Sesión posible bloqueadora: pid=%s state=%s wait=%s query=%s",
                row.pid,
                row.state,
                row.wait_event_type,
                row.query,
            )
    except Exception:  # pragma: no cover - diagnóstico best-effort
        logger.warning("No se pudieron consultar sesiones bloqueadoras.")


def run_migrations_online() -> None:
    """Ejecuta migraciones en modo online sobre la base configurada."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_postgres_url()

    settings = get_settings()
    connect_args = build_postgres_connect_args(
        settings,
        server_settings=_migration_server_settings(),
    )

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    target = _describe_postgres_target(configuration["sqlalchemy.url"])[1]

    with connectable.connect() as connection:
        _log_effective_gucs(connection, target)

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table=_get_alembic_version_table(),
        )

        try:
            with context.begin_transaction():
                # Advisory lock con alcance de transacción: se auto-libera al
                # commit/rollback o al desconectar, así un pod muerto no deja el
                # lock tomado bloqueando a los siguientes.
                connection.exec_driver_sql(
                    f"SELECT pg_advisory_xact_lock(hashtext('{_ADVISORY_LOCK_KEY}'))"
                )
                context.run_migrations()
        except Exception:
            _log_blocking_sessions(connectable)
            raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()