"""Fixtures compartidos para reducir duplicación en la suite de pruebas."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from coderag.core.settings import Settings


_RUN_REMOTE_E2E = os.environ.get("RUN_REMOTE_E2E", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

if not _RUN_REMOTE_E2E:
    for env_var in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "CHROMA_MODE",
        "CHROMA_HOST",
        "CHROMA_PORT",
        "CHROMA_TOKEN",
        "CHROMA_USERNAME",
        "CHROMA_PASSWORD",
        "CHROMA_REMOTE_BATCH_SIZE_OVERRIDE",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "REDIS_URL",
    ):
        os.environ.pop(env_var, None)


def _test_postgres_env(name: str, default: str) -> str:
    """Resuelve valores Postgres de prueba con fallback estable."""
    value = os.environ.get(name, default).strip()
    return value or default


def build_test_postgres_settings(**overrides: Any) -> Settings:
    """Crea Settings mínimos para derivar DSN de Postgres en pruebas."""
    values: dict[str, Any] = {
        "POSTGRES_HOST": _test_postgres_env("POSTGRES_HOST", "localhost"),
        "POSTGRES_PORT": int(_test_postgres_env("POSTGRES_PORT", "5432")),
        "POSTGRES_DB": _test_postgres_env("POSTGRES_DB", "coderag"),
        "POSTGRES_USER": _test_postgres_env("POSTGRES_USER", "coderag"),
        "POSTGRES_PASSWORD": _test_postgres_env(
            "POSTGRES_PASSWORD",
            "coderag",
        ),
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def build_test_postgres_dsn(**overrides: Any) -> str:
    """Construye una DSN PostgreSQL a partir de POSTGRES_* para tests."""
    return build_test_postgres_settings(**overrides).resolve_postgres_dsn()


def build_test_postgres_target(**overrides: Any) -> str:
    """Devuelve el destino saneado host:puerto/base derivado de la DSN."""
    parsed = urlsplit(build_test_postgres_dsn(**overrides))
    host = parsed.hostname or ""
    port = parsed.port or 5432
    database = unquote(parsed.path.lstrip("/"))
    return f"{host}:{port}/{database}"


@pytest.fixture
def make_test_settings(tmp_path: Path):
    """Construye objetos livianos de settings para pruebas unitarias."""

    def factory(**overrides: Any) -> SimpleNamespace:
        """Crea settings con workspace temporal y atributos opcionales."""
        workspace_path = Path(
            overrides.pop("workspace_path", tmp_path / "workspace")
        )
        workspace_path.mkdir(parents=True, exist_ok=True)

        values: dict[str, Any] = {
            "workspace_path": workspace_path,
            "embedding_provider": "vertex",
            "embedding_model": "text-embedding-005",
            "retain_workspace_after_ingest": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    return factory


@pytest.fixture
def patch_module_settings(monkeypatch: pytest.MonkeyPatch, make_test_settings):
    """Parchea get_settings() de un módulo con settings temporales."""

    def factory(target_module: Any, **overrides: Any) -> SimpleNamespace:
        """Devuelve el objeto de settings aplicado al módulo indicado."""
        settings = make_test_settings(**overrides)
        monkeypatch.setattr(target_module, "get_settings", lambda: settings)
        return settings

    return factory


@pytest.fixture
def sync_thread_class():
    """Expone un hilo síncrono para ejecutar jobs inline durante pruebas."""

    class SyncThread:
        """Reemplazo mínimo de Thread que ejecuta start() inline."""

        def __init__(self, target, args, daemon):
            """Guarda el target y sus argumentos sin lanzar un hilo real."""
            del daemon
            self._target = target
            self._args = args

        def start(self) -> None:
            """Ejecuta el target inmediatamente en el mismo hilo de prueba."""
            self._target(*self._args)

    return SyncThread