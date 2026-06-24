"""Infraestructura compartida de SQLAlchemy para conexiones PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from sqlalchemy.orm import Session, sessionmaker

from coderag.core.settings import resolve_postgres_dsn


def _describe_postgres_target(postgres_dsn: str) -> tuple[str, str]:
    """Resume el destino del DSN sin exponer credenciales."""
    parsed = urlsplit(postgres_dsn)
    host = parsed.hostname or "<unknown-host>"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/") or "<unknown-db>"
    return host, f"{host}:{port}/{database}"


def to_sqlalchemy_postgres_url(postgres_dsn: str) -> str:
    """Adapta la DSN legacy a un URL explícito para SQLAlchemy + psycopg."""
    normalized = postgres_dsn.strip()
    if normalized.startswith("postgresql+psycopg://"):
        return normalized
    if normalized.startswith("postgresql://"):
        return normalized.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    if normalized.startswith("postgres://"):
        return normalized.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )
    return normalized


# Afinamiento de keepalives TCP (constantes razonables; el idle es configurable).
_TCP_KEEPALIVES_INTERVAL_SECONDS = 10
_TCP_KEEPALIVES_COUNT = 5

# SQLSTATE de Postgres que indican objetos duplicados (colisión de nombres).
_DUPLICATE_OBJECT_SQLSTATES = frozenset({"42P07", "42710", "42P06", "42P16"})
_CONNECTION_ERROR_TOKENS = (
    "server closed the connection",
    "connection reset",
    "ssl connection has been closed",
    "could not receive data",
    "connection timeout",
    "terminating connection",
)


def extract_sqlstate(exc: BaseException) -> str | None:
    """Extrae el SQLSTATE de una excepción DBAPI/psycopg si está disponible."""
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(exc, "sqlstate", None)


def classify_postgres_failure(exc: BaseException) -> str | None:
    """Devuelve una pista accionable según el tipo de error, o None.

    Distingue un error DDL determinista (objeto ya existe → colisión de nombres
    en base compartida) de un corte de red/service mesh, para que el log de
    fallo de migración sea accionable en vez de genérico.
    """
    sqlstate = extract_sqlstate(exc)
    orig = getattr(exc, "orig", None)
    detail = (str(orig) if orig is not None else str(exc)).lower()

    if sqlstate in _DUPLICATE_OBJECT_SQLSTATES or "already exists" in detail:
        return (
            "error DDL determinista (el objeto ya existe; posible colisión de "
            "nombres en la base compartida). Revisar si la tabla o índices de la "
            "migración ya están creados en la base."
        )
    if isinstance(exc, SqlAlchemyOperationalError) or any(
        token in detail for token in _CONNECTION_ERROR_TOKENS
    ):
        return (
            "error de conexión (posible corte de red/service mesh en el "
            "statement de esta migración)."
        )
    return None


def build_postgres_connect_args(
    settings: object,
    *,
    server_settings: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Construye connect_args de libpq/psycopg resilientes al service mesh.

    Incluye keepalives TCP y tcp_user_timeout para detectar peers muertos a
    nivel de socket, connect_timeout para acotar el establecimiento, y GUCs de
    servidor (lock/statement/idle_in_transaction timeouts) vía ``options`` para
    que apliquen desde la conexión. ``server_settings`` permite inyectar GUCs
    extra (p.ej. para la conexión de migración).
    """
    connect_timeout = _coerce_positive_int(
        getattr(settings, "postgres_connect_timeout_seconds", 10), 10
    )
    keepalives_idle = _coerce_positive_int(
        getattr(settings, "postgres_tcp_keepalives_idle_seconds", 30), 30
    )
    tcp_user_timeout_ms = _coerce_positive_int(
        getattr(settings, "postgres_tcp_user_timeout_ms", 30000), 30000
    )

    options_parts = [f"-c tcp_user_timeout={tcp_user_timeout_ms}"]
    for guc_name, guc_value in (server_settings or {}).items():
        options_parts.append(f"-c {guc_name}={guc_value}")

    return {
        "connect_timeout": connect_timeout,
        "keepalives": 1,
        "keepalives_idle": keepalives_idle,
        "keepalives_interval": _TCP_KEEPALIVES_INTERVAL_SECONDS,
        "keepalives_count": _TCP_KEEPALIVES_COUNT,
        "options": " ".join(options_parts),
    }


def _coerce_positive_int(value: Any, default: int) -> int:
    """Normaliza un entero positivo o retorna un default seguro."""
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _coerce_positive_float(value: Any, default: float) -> float:
    """Normaliza un float positivo o retorna un default seguro."""
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


class PostgresSessionFactory:
    """Administra engine y sesiones SQLAlchemy para PostgreSQL."""

    def __init__(
        self,
        postgres_dsn: str,
        *,
        pool_size: int = 5,
        pool_timeout: float = 30.0,
        connect_args: dict[str, Any] | None = None,
    ) -> None:
        """Construye un factory reutilizable para conexiones a Postgres."""
        self._url = postgres_dsn
        self._pool_size = _coerce_positive_int(pool_size, 5)
        self._pool_timeout = _coerce_positive_float(pool_timeout, 30.0)
        self._connect_args = dict(connect_args or {})
        self._engine = self._build_engine()
        self._session_factory = sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    @classmethod
    def from_settings(cls, settings: object) -> "PostgresSessionFactory":
        """Construye el factory desde Settings reales o doubles de prueba."""
        postgres_dsn = resolve_postgres_dsn(settings)
        if not postgres_dsn:
            raise ValueError(
                "No se pudo construir PostgresSessionFactory: DSN vacía. "
                "Configura POSTGRES_HOST y credenciales válidas."
            )

        return cls(
            postgres_dsn,
            pool_size=getattr(settings, "postgres_pool_size", 5),
            pool_timeout=getattr(settings, "postgres_pool_timeout", 30.0),
            connect_args=build_postgres_connect_args(settings),
        )

    @property
    def engine(self) -> Engine:
        """Expone el engine compartido para casos que requieran SQL Core."""
        return self._engine

    def _build_engine(self) -> Engine:
        """Crea el engine SQLAlchemy con pool configurado."""
        return create_engine(
            to_sqlalchemy_postgres_url(self._url),
            pool_pre_ping=True,
            pool_size=self._pool_size,
            pool_timeout=self._pool_timeout,
            connect_args=self._connect_args,
        )

    @contextmanager
    def get_session(self) -> Iterator[Session]:
        """Abre una sesión SQLAlchemy con manejo uniforme de errores."""
        session = self._session_factory()
        try:
            yield session
        except SqlAlchemyOperationalError as exc:
            raise self._build_connection_error(exc) from exc
        finally:
            session.close()

    @contextmanager
    def get_connection(self) -> Iterator[Connection]:
        """Abre una conexión SQLAlchemy para SQL Core o texto especializado."""
        try:
            with self._engine.begin() as connection:
                yield connection
        except SqlAlchemyOperationalError as exc:
            raise self._build_connection_error(exc) from exc

    def _build_connection_error(self, exc: Exception) -> RuntimeError:
        """Normaliza errores operativos evitando exponer credenciales."""
        host, target = _describe_postgres_target(self._url)
        compose_hint = ""
        if host == "postgres":
            compose_hint = (
                " Si usas docker-compose, el host 'postgres' solo existe "
                "cuando el perfil 'remote' está activo."
            )
        return RuntimeError(
            "No se pudo conectar a Postgres en "
            f"{target}. Verifica POSTGRES_HOST/POSTGRES_PORT y que el "
            f"host sea resolvible desde este runtime.{compose_hint} "
            f"Error original: {exc}"
        )