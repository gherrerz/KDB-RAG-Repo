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
from coderag.ingestion.index_bm25 import BM25Index


def _cursor(rows=None, rowcount: int = 0) -> MagicMock:
    """Devuelve un cursor mock compatible con psycopg para pruebas."""
    cursor = MagicMock()
    cursor.fetchall.return_value = list(rows or [])
    cursor.fetchone.return_value = (rows[0] if rows else None)
    cursor.rowcount = rowcount
    return cursor


def _conn(cursor: MagicMock | None = None) -> MagicMock:
    """Crea una conexión mock que soporta context manager."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    if cursor is not None:
        conn.execute.return_value = cursor
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
    return conn


def test_bm25_index_satisfies_repository_lexical_index(
    patch_module_settings,
) -> None:
    """BM25 expone el contrato mínimo de query y delete por repositorio."""
    import coderag.ingestion.index_bm25 as module

    patch_module_settings(module)
    index = BM25Index()
    index.build(
        repo_id="repo-1",
        docs=["alpha beta", "gamma delta"],
        metadatas=[{"id": "repo-1:1"}, {"id": "repo-1:2"}],
    )

    assert isinstance(index, RepositoryLexicalIndex)
    results = index.query(repo_id="repo-1", text="alpha")
    deleted = index.delete_repo("repo-1")

    assert results[0]["id"] == "repo-1:1"
    assert {"id", "text", "score", "metadata"}.issubset(results[0])
    assert deleted["docs_removed"] == 2


def test_lexical_store_satisfies_repository_lexical_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """LexicalStore respeta el mismo shape mínimo de contrato léxico."""
    patch_connect = "coderag.storage.lexical_store.psycopg.connect"
    init_conn = _conn(_cursor())
    row = {
        "id": "repo-1:1",
        "doc": "alpha beta",
        "path": "src/a.py",
        "symbol_name": "alpha",
        "entity_type": "symbol",
        "metadata": '{"id":"repo-1:1","path":"src/a.py"}',
        "score": 0.9,
    }
    query_conn = _conn(_cursor(rows=[row], rowcount=1))

    from coderag.storage.lexical_store import LexicalStore

    with patch(patch_connect, return_value=init_conn):
        store = LexicalStore("postgresql://fake/db")

    assert isinstance(store, RepositoryLexicalIndex)

    with patch(patch_connect, return_value=query_conn):
        results = store.query(repo_id="repo-1", text="alpha")

    with patch(patch_connect, return_value=_conn(_cursor(rowcount=1))):
        deleted = store.delete_repo("repo-1")

    assert results[0]["id"] == "repo-1:1"
    assert {"id", "text", "score", "metadata"}.issubset(results[0])
    assert deleted == {"docs_removed": 1}


def test_build_repository_lexical_index_returns_bm25_without_postgres(
    tmp_path: Path,
) -> None:
    """Sin DSN de Postgres, la factory retorna el backend BM25 global."""
    settings = SimpleNamespace(workspace_path=tmp_path / "workspace")

    index = build_repository_lexical_index(settings)

    from coderag.ingestion.index_bm25 import GLOBAL_BM25

    assert index is GLOBAL_BM25
    assert isinstance(index, RepositoryLexicalIndex)


def test_build_repository_lexical_index_returns_lexical_store_with_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con DSN resuelto, la factory selecciona LexicalStore."""

    class FakeLexicalStore:
        """Stub mínimo para verificar selección del backend PostgreSQL."""

        def __init__(self, dsn: str, language: str) -> None:
            self.dsn = dsn
            self.language = language

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


def test_repository_lexical_backend_label_matches_selected_backend(
    tmp_path: Path,
) -> None:
    """Expone una etiqueta estable para el backend léxico activo."""
    sqlite_settings = SimpleNamespace(workspace_path=tmp_path / "workspace")
    postgres_settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace-postgres",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    assert repository_lexical_backend_label(sqlite_settings) == "bm25"
    assert repository_lexical_backend_label(postgres_settings) == "lexical"


def test_repository_has_active_lexical_data_uses_bm25_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin Postgres, consulta el estado del backend BM25 global."""
    import coderag.core.lexical_index as module

    settings = SimpleNamespace(workspace_path=tmp_path / "workspace")
    monkeypatch.setattr(module.GLOBAL_BM25, "has_repo", lambda repo_id: False)
    monkeypatch.setattr(
        module.GLOBAL_BM25,
        "has_repo_snapshot",
        lambda repo_id: repo_id == "repo-1",
    )
    monkeypatch.setattr(
        module.GLOBAL_BM25,
        "delete_repo",
        lambda repo_id: {"docs_removed": 2, "snapshot_removed": 1},
    )

    assert repository_has_active_lexical_data(settings, "repo-1") is True
    assert delete_active_repository_lexical_data(settings, "repo-1") == {
        "docs_removed": 2,
        "snapshot_removed": 1,
    }


def test_repository_has_query_ready_lexical_data_uses_optional_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Usa ensure_repo_loaded cuando el backend activo lo expone."""
    import coderag.core.lexical_index as module

    settings = SimpleNamespace(workspace_path=tmp_path / "workspace")
    monkeypatch.setattr(
        module.GLOBAL_BM25,
        "ensure_repo_loaded",
        lambda repo_id: repo_id == "repo-1",
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

        def __init__(self, dsn: str, language: str) -> None:
            self.dsn = dsn
            self.language = language

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