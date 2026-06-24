"""Entorno Alembic para versionar el esquema PostgreSQL operativo."""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Engine, engine_from_config, pool, text

from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.storage.postgres_schema import POSTGRES_SCHEMA_METADATA
from coderag.storage.postgres_session import (
    _describe_postgres_target,
    build_postgres_connect_args,
    classify_postgres_failure,
    extract_sqlstate,
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


def _log_effective_gucs(engine: Engine, target: str) -> None:
    """Loguea destino y GUCs efectivos: confirma código vivo y estado real.

    Usa una conexión propia (los GUCs vienen de las ``options`` de conexión, así
    que son idénticos en cualquier conexión del engine) para no interferir con el
    estado transaccional de la conexión que ejecuta las migraciones.
    """
    try:
        with engine.connect() as diag:
            lock_timeout = diag.exec_driver_sql("SHOW lock_timeout").scalar()
            statement_timeout = diag.exec_driver_sql(
                "SHOW statement_timeout"
            ).scalar()
            idle_tx_timeout = diag.exec_driver_sql(
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


def _log_migration_exception(exc: BaseException) -> None:
    """Loguea la excepción REAL de la migración con clase, mensaje y SQLSTATE.

    Sustituye al mensaje genérico anterior: la causa de un fallo de migración
    (DDL determinista vs. corte de red/mesh vs. lock) solo es distinguible si se
    expone la excepción concreta. ``exc.orig`` trae el error DBAPI/psycopg con
    ``sqlstate`` cuando aplica.
    """
    orig = getattr(exc, "orig", None)
    detail = str(orig) if orig is not None else str(exc)
    logger.error(
        "Migración fallida en este intento: %s: %s (SQLSTATE=%s)",
        type(exc).__name__,
        detail.strip(),
        extract_sqlstate(exc),
    )
    hint = classify_postgres_failure(exc)
    if hint:
        logger.error("Diagnóstico: %s", hint)


def _log_blocking_sessions(engine: Engine) -> None:
    """Loguea sesiones activas/bloqueadoras como dato adicional de diagnóstico."""
    query = text(
        "SELECT pid, state, wait_event_type, wait_event, "
        "left(query, 200) AS query "
        "FROM pg_stat_activity "
        "WHERE datname = current_database() "
        "AND pid <> pg_backend_pid() "
        "AND state IS DISTINCT FROM 'idle'"
    )
    try:
        # Conexión separada: la de migración puede estar en transacción abortada.
        with engine.connect() as diag:
            rows = diag.execute(query).fetchall()
        if not rows:
            logger.info(
                "Sin otras sesiones activas en la base al momento del fallo."
            )
            return
        for row in rows:
            logger.error(
                "Sesión concurrente: pid=%s state=%s wait=%s/%s query=%s",
                row.pid,
                row.state,
                row.wait_event_type,
                row.wait_event,
                row.query,
            )
    except Exception:  # pragma: no cover - diagnóstico best-effort
        logger.warning("No se pudieron consultar sesiones concurrentes.")


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
    _log_effective_gucs(connectable, target)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table=_get_alembic_version_table(),
            # Cada migración en su propia transacción: 0001-0003 commitean y
            # persisten, y un fallo (p.ej. 0004) queda aislado y atribuido a esa
            # migración, sin re-ejecutar todo en el siguiente arranque.
            transaction_per_migration=True,
        )

        try:
            context.run_migrations()
        except Exception as exc:
            _log_migration_exception(exc)
            _log_blocking_sessions(connectable)
            raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()