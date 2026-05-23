"""Pruebas de contrato y factory para backends léxicos por repositorio."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import MagicMock, patch

import pytest

from coderag.core.lexical_index import (
    RepositoryLexicalIndex,
    build_repository_lexical_index,
    delete_active_repository_lexical_data,
    ensure_repository_lexical_index_loaded,
    repository_has_active_lexical_data,
    repository_has_query_ready_lexical_data,
    repository_lexical_backend_label,
)


def _session_factory_mock(connection: MagicMock) -> MagicMock:
    """Crea un session factory mock con contexto de conexión."""
    factory = MagicMock()
    factory.get_connection.return_value.__enter__.return_value = connection
    factory.get_connection.return_value.__exit__.return_value = False
    return factory


def test_lexical_store_satisfies_repository_lexical_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """LexicalStore respeta el mismo shape mínimo de contrato léxico."""
    row = {
        "id": "repo-1:1",
        "doc": "alpha beta",
        "path": "src/a.py",
        "symbol_name": "alpha",
        "entity_type": "symbol",
        "metadata": '{"id":"repo-1:1","path":"src/a.py"}',
        "score": 0.9,
    }
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.all.return_value = [
        row
    ]

    from coderag.storage.lexical_store import LexicalStore

    store = LexicalStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(connection),
    )

    assert isinstance(store, RepositoryLexicalIndex)

    results = store.query(repo_id="repo-1", text="alpha")
    connection.execute.return_value.rowcount = 1
    deleted = store.delete_repo("repo-1")

    assert results[0]["id"] == "repo-1:1"
    assert {"id", "text", "score", "metadata"}.issubset(results[0])
    assert deleted == {"docs_removed": 1}


def test_build_repository_lexical_index_requires_postgres(
    tmp_path: Path,
) -> None:
    """Sin Postgres configurado, el runtime ya no debe resolver BM25."""
    settings = SimpleNamespace(workspace_path=tmp_path / "workspace")

    with pytest.raises(RuntimeError, match="LexicalStore Postgres es obligatorio"):
        build_repository_lexical_index(settings)


def test_build_repository_lexical_index_returns_lexical_store_with_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con DSN resuelto, la factory selecciona LexicalStore."""

    class FakeLexicalStore:
        """Stub mínimo para verificar selección del backend PostgreSQL."""

        def __init__(
            self,
            dsn: str,
            language: str,
            session_factory=None,
        ) -> None:
            self.dsn = dsn
            self.language = language
            self.session_factory = session_factory

        def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
            del repo_id, text, top_n
            return []

        def delete_repo(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"docs_removed": 0}

    fake_module = ModuleType("coderag.storage.lexical_store")
    fake_module.LexicalStore = FakeLexicalStore
    monkeypatch.setitem(sys.modules, "coderag.storage.lexical_store", fake_module)

    settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace",
        lexical_fts_language="spanish",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    index = build_repository_lexical_index(settings)

    assert isinstance(index, FakeLexicalStore)
    assert index.dsn == "postgresql://fake/db"
    assert index.language == "spanish"
    assert index.session_factory is not None


def test_build_repository_lexical_index_ignores_removed_legacy_flag_when_postgres_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La presencia residual del flag no debe reactivar BM25 en runtime."""

    class FakeLexicalStore:
        def __init__(self, dsn: str, language: str, session_factory=None) -> None:
            self.dsn = dsn
            self.language = language
            self.session_factory = session_factory

        def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
            del repo_id, text, top_n
            return []

        def delete_repo(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"docs_removed": 0}

    fake_module = ModuleType("coderag.storage.lexical_store")
    fake_module.LexicalStore = FakeLexicalStore
    monkeypatch.setitem(sys.modules, "coderag.storage.lexical_store", fake_module)

    settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace-postgres",
        lexical_fts_language="spanish",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    index = build_repository_lexical_index(settings)

    assert isinstance(index, FakeLexicalStore)


def test_repository_lexical_backend_label_matches_selected_backend(
    tmp_path: Path,
) -> None:
    """Expone una etiqueta estable para el backend léxico activo."""
    sqlite_settings = SimpleNamespace(workspace_path=tmp_path / "workspace")
    postgres_settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace-postgres",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    assert repository_lexical_backend_label(sqlite_settings) == "lexical_unavailable"
    assert repository_lexical_backend_label(postgres_settings) == "lexical"


def test_repository_lexical_backend_label_matches_postgres_runtime(
    tmp_path: Path,
) -> None:
    """La etiqueta visible refleja el runtime Postgres cuando hay DSN."""
    settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace-postgres",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    assert repository_lexical_backend_label(settings) == "lexical"


def test_repository_has_active_lexical_data_requires_postgres(
    tmp_path: Path,
) -> None:
    """Los helpers léxicos ya no deben resolver estado sin Postgres."""

    settings = SimpleNamespace(workspace_path=tmp_path / "workspace")

    with pytest.raises(RuntimeError, match="LexicalStore Postgres es obligatorio"):
        repository_has_active_lexical_data(settings, "repo-1")
    with pytest.raises(RuntimeError, match="LexicalStore Postgres es obligatorio"):
        delete_active_repository_lexical_data(settings, "repo-1")


def test_repository_has_query_ready_lexical_data_uses_optional_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Usa ensure_repo_loaded cuando LexicalStore lo expone."""

    class FakeLexicalStore:
        def __init__(self, dsn: str, language: str, session_factory=None) -> None:
            self.dsn = dsn
            self.language = language
            self.session_factory = session_factory

        def ensure_repo_loaded(self, repo_id: str) -> bool:
            return repo_id == "repo-1"

        def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
            del repo_id, text, top_n
            return []

        def delete_repo(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"docs_removed": 0}

    fake_module = ModuleType("coderag.storage.lexical_store")
    fake_module.LexicalStore = FakeLexicalStore
    monkeypatch.setitem(sys.modules, "coderag.storage.lexical_store", fake_module)

    settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace-postgres",
        lexical_fts_language="english",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    assert repository_has_query_ready_lexical_data(settings, "repo-1") is True
    assert repository_has_query_ready_lexical_data(settings, "repo-2") is False


def test_repository_has_active_lexical_data_uses_lexical_store_with_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con Postgres resuelto, consulta y borra vía LexicalStore."""

    class FakeLexicalStore:
        """Stub mínimo para verificar helpers de backend léxico activo."""

        def __init__(
            self,
            dsn: str,
            language: str,
            session_factory=None,
        ) -> None:
            self.dsn = dsn
            self.language = language
            self.session_factory = session_factory

        def has_corpus(self, repo_id: str) -> bool:
            return repo_id == "repo-1"

        def delete_repo(self, repo_id: str) -> dict[str, int]:
            assert repo_id == "repo-1"
            return {"docs_removed": 3}

        def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
            del repo_id, text, top_n
            return []

    fake_module = ModuleType("coderag.storage.lexical_store")
    fake_module.LexicalStore = FakeLexicalStore
    monkeypatch.setitem(sys.modules, "coderag.storage.lexical_store", fake_module)

    settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace-postgres",
        lexical_fts_language="spanish",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    assert repository_has_active_lexical_data(settings, "repo-1") is True
    assert repository_has_query_ready_lexical_data(settings, "repo-1") is True
    assert delete_active_repository_lexical_data(settings, "repo-1") == {
        "docs_removed": 3,
    }


def test_ensure_repository_lexical_index_loaded_calls_optional_loader() -> None:
    """El helper debe invocar ensure_repo_loaded solo cuando existe."""

    class FakeIndex:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def ensure_repo_loaded(self, repo_id: str) -> None:
            self.calls.append(repo_id)

        def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
            del repo_id, text, top_n
            return []

        def delete_repo(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"docs_removed": 0}

    index = FakeIndex()

    ensure_repository_lexical_index_loaded(index, "repo-1")

    assert index.calls == ["repo-1"]