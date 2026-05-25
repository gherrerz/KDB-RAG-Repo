"""Pruebas unitarias para la infraestructura compartida de sesiones Postgres."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from conftest import build_test_postgres_dsn, build_test_postgres_target
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError

from coderag.storage.postgres_session import (
    PostgresSessionFactory,
    to_sqlalchemy_postgres_url,
)


_PATCH_CREATE_ENGINE = "coderag.storage.postgres_session.create_engine"


def _fake_engine() -> MagicMock:
    """Construye un engine mock mínimo para pruebas unitarias."""
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = MagicMock()
    engine.begin.return_value.__exit__.return_value = False
    return engine


def test_from_settings_uses_resolved_dsn_and_pool_values() -> None:
    """El factory debe consumir DSN y settings de pool del objeto settings."""
    postgres_dsn = build_test_postgres_dsn(POSTGRES_DB="db")
    settings = SimpleNamespace(
        resolve_postgres_dsn=lambda: postgres_dsn,
        postgres_pool_size=11,
        postgres_pool_timeout=42.5,
    )
    engine = _fake_engine()

    with patch(_PATCH_CREATE_ENGINE, return_value=engine) as create_engine_mock:
        factory = PostgresSessionFactory.from_settings(settings)

    assert factory.engine is engine
    create_engine_mock.assert_called_once_with(
        to_sqlalchemy_postgres_url(postgres_dsn),
        pool_pre_ping=True,
        pool_size=11,
        pool_timeout=42.5,
    )


def test_from_settings_raises_when_dsn_is_empty() -> None:
    """El factory debe fallar de forma explícita si no hay DSN efectiva."""
    settings = SimpleNamespace(resolve_postgres_dsn=lambda: "")

    with pytest.raises(ValueError) as exc_info:
        PostgresSessionFactory.from_settings(settings)

    assert "DSN vacía" in str(exc_info.value)


def test_invalid_pool_values_fall_back_to_safe_defaults() -> None:
    """Valores inválidos de pool deben normalizarse a defaults seguros."""
    engine = _fake_engine()
    postgres_dsn = build_test_postgres_dsn(POSTGRES_DB="db")

    with patch(_PATCH_CREATE_ENGINE, return_value=engine) as create_engine_mock:
        PostgresSessionFactory(
            postgres_dsn,
            pool_size=0,
            pool_timeout=-1,
        )

    create_engine_mock.assert_called_once_with(
        to_sqlalchemy_postgres_url(postgres_dsn),
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30.0,
    )


def test_sqlalchemy_url_uses_psycopg_driver() -> None:
    """La DSN legacy debe adaptarse al driver explícito requerido por SQLAlchemy."""
    postgres_dsn = build_test_postgres_dsn(POSTGRES_DB="db")
    sqlalchemy_dsn = to_sqlalchemy_postgres_url(postgres_dsn)

    assert sqlalchemy_dsn == postgres_dsn.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1,
    )
    assert (
        to_sqlalchemy_postgres_url(
            postgres_dsn.replace("postgresql://", "postgres://", 1)
        )
        == sqlalchemy_dsn
    )
    assert to_sqlalchemy_postgres_url(sqlalchemy_dsn) == sqlalchemy_dsn


def test_get_connection_wraps_operational_error_without_credentials() -> None:
    """Errores operativos deben sanear credenciales y mantener destino."""
    engine = _fake_engine()
    postgres_dsn = build_test_postgres_dsn(
        POSTGRES_HOST="postgres",
        POSTGRES_DB="coderag",
        POSTGRES_USER="coderag",
        POSTGRES_PASSWORD="secret",
    )
    engine.begin.side_effect = SqlAlchemyOperationalError(
        statement=None,
        params=None,
        orig=RuntimeError("timeout"),
    )

    with patch(_PATCH_CREATE_ENGINE, return_value=engine):
        factory = PostgresSessionFactory(postgres_dsn)

    with pytest.raises(RuntimeError) as exc_info:
        with factory.get_connection():
            pass

    message = str(exc_info.value)
    assert (
        build_test_postgres_target(
            POSTGRES_HOST="postgres",
            POSTGRES_DB="coderag",
            POSTGRES_USER="coderag",
            POSTGRES_PASSWORD="secret",
        )
        in message
    )
    assert "perfil 'remote'" in message
    assert "secret" not in message


def test_get_session_closes_session_after_use() -> None:
    """La sesión debe cerrarse siempre al salir del context manager."""
    engine = _fake_engine()

    with patch(_PATCH_CREATE_ENGINE, return_value=engine):
        factory = PostgresSessionFactory(build_test_postgres_dsn(POSTGRES_DB="db"))

    with patch.object(factory, "_session_factory") as session_factory_mock:
        session = MagicMock()
        session_factory_mock.return_value = session

        with factory.get_session() as active_session:
            assert active_session is session

        session.close.assert_called_once_with()