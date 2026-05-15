"""Fixtures compartidos para reducir duplicación en la suite de pruebas."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


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
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "REDIS_URL",
    ):
        os.environ.pop(env_var, None)


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