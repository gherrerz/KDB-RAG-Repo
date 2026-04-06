"""Pruebas para operaciones de limpieza por repositorio en BM25."""

from pathlib import Path

import pytest

from src.coderag.ingestion.index_bm25 import BM25Index


class _Settings:
    """Configuración mínima para pruebas de snapshots BM25."""

    def __init__(self, workspace_path: Path) -> None:
        """Guarda ruta de workspace usada para snapshots de prueba."""
        self.workspace_path = workspace_path


def test_delete_repo_removes_memory_and_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Elimina datos del repo en memoria y snapshot persistido en disco."""
    settings = _Settings(workspace_path=tmp_path / "workspace")
    settings.workspace_path.mkdir(parents=True, exist_ok=True)

    import src.coderag.ingestion.index_bm25 as module

    monkeypatch.setattr(module, "get_settings", lambda: settings)

    index = BM25Index()
    docs = ["alpha beta", "gamma"]
    metadatas = [{"id": "r1:1"}, {"id": "r1:2"}]
    index.build(repo_id="r1", docs=docs, metadatas=metadatas)
    persisted = index.persist_repo("r1")

    assert persisted is True
    assert index.has_repo("r1") is True
    assert index.has_repo_snapshot("r1") is True

    result = index.delete_repo("r1")

    assert result["docs_removed"] == 2
    assert result["snapshot_removed"] == 1
    assert index.has_repo("r1") is False
    assert index.has_repo_snapshot("r1") is False


def test_delete_repo_is_idempotent_on_missing_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No falla al borrar un repo inexistente y devuelve conteo en cero."""
    settings = _Settings(workspace_path=tmp_path / "workspace")
    settings.workspace_path.mkdir(parents=True, exist_ok=True)

    import src.coderag.ingestion.index_bm25 as module

    monkeypatch.setattr(module, "get_settings", lambda: settings)

    index = BM25Index()
    result = index.delete_repo("missing")

    assert result == {"docs_removed": 0, "snapshot_removed": 0}
