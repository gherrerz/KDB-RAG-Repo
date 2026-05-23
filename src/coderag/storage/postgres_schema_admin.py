"""Operaciones administrativas del esquema PostgreSQL via Alembic."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal, Sequence

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.storage.postgres_session import (
    PostgresSessionFactory,
    to_sqlalchemy_postgres_url,
)
from coderag.storage.postgres_startup import ensure_postgres_schema_ready


SchemaAdminCommand = Literal["current", "upgrade", "stamp", "validate"]


class _ValidationSettingsProxy:
    """Fuerza politica validate sin alterar el objeto settings original."""

    def __init__(self, settings: object) -> None:
        """Envuelve settings para reutilizar el bootstrap en modo validate."""
        self._settings = settings
        self.runtime_environment = "production"
        self.postgres_pool_size = getattr(settings, "postgres_pool_size", 5)
        self.postgres_pool_timeout = getattr(
            settings,
            "postgres_pool_timeout",
            30.0,
        )

    def __getattr__(self, name: str) -> Any:
        """Delega atributos no sobrescritos al settings original."""
        return getattr(self._settings, name)

    def resolve_postgres_dsn(self) -> str:
        """Mantiene la misma resolucion DSN usada por el runtime."""
        return resolve_postgres_dsn(self._settings)

    def resolve_postgres_startup_policy(self) -> str:
        """Fuerza validacion externa del esquema sin auto-migrar."""
        return "validate"


def _repo_root() -> Path:
    """Resuelve la raiz del repo a partir del layout src actual."""
    return Path(__file__).resolve().parents[3]


def _build_alembic_config(postgres_dsn: str) -> Config:
    """Construye Config de Alembic usando la DSN efectiva actual."""
    config = Config(str(_repo_root() / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        to_sqlalchemy_postgres_url(postgres_dsn),
    )
    return config


def _read_database_heads(factory: PostgresSessionFactory) -> set[str]:
    """Lee las revisiones aplicadas hoy en la base activa."""
    with factory.get_connection() as connection:
        from alembic.runtime.migration import MigrationContext

        context = MigrationContext.configure(connection)
        return set(context.get_current_heads())


def build_postgres_schema_admin_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI para operaciones de esquema PostgreSQL."""
    parser = argparse.ArgumentParser(
        description=(
            "Administra el esquema PostgreSQL del runtime con la misma "
            "resolucion de settings usada por la aplicacion."
        )
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    subparsers.add_parser(
        "current",
        help="muestra revisiones aplicadas y heads esperados",
    )
    subparsers.add_parser(
        "validate",
        help="valida que la base ya este alineada sin modificarla",
    )

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="ejecuta alembic upgrade sobre la revision indicada",
    )
    upgrade_parser.add_argument(
        "revision",
        nargs="?",
        default="head",
        help="revision destino; default: head",
    )

    stamp_parser = subparsers.add_parser(
        "stamp",
        help="marca la revision sin ejecutar migraciones",
    )
    stamp_parser.add_argument(
        "revision",
        nargs="?",
        default="head",
        help="revision a marcar; default: head",
    )

    return parser


def run_postgres_schema_command(
    settings: object,
    *,
    operation: SchemaAdminCommand,
    revision: str = "head",
) -> dict[str, Any]:
    """Ejecuta una operacion administrativa de esquema para Postgres."""
    postgres_dsn = resolve_postgres_dsn(settings)
    if not postgres_dsn:
        raise ValueError(
            "POSTGRES_HOST y credenciales validas son obligatorios para "
            "administrar el esquema PostgreSQL."
        )

    if operation == "validate":
        report = ensure_postgres_schema_ready(
            _ValidationSettingsProxy(settings),
            force=True,
        )
        return {
            **report,
            "command": operation,
        }

    factory = PostgresSessionFactory.from_settings(settings)
    config = _build_alembic_config(postgres_dsn)
    expected_heads = sorted(ScriptDirectory.from_config(config).get_heads())

    if operation == "upgrade":
        command.upgrade(config, revision)
    elif operation == "stamp":
        command.stamp(config, revision)
    elif operation != "current":
        raise ValueError(f"Operacion de esquema no soportada: {operation}")

    current_heads = sorted(_read_database_heads(factory))
    report = {
        "enabled": True,
        "command": operation,
        "current_heads": current_heads,
        "expected_heads": expected_heads,
    }
    if operation in {"upgrade", "stamp"}:
        report["revision"] = revision
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada CLI para operacion manual de Alembic/Postgres."""
    parser = build_postgres_schema_admin_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    revision = getattr(args, "revision", "head")

    try:
        report = run_postgres_schema_command(
            settings,
            operation=args.operation,
            revision=revision,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())