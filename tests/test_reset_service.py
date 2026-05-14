"""Pruebas unitarias focalizadas para limpieza de storage."""

from types import ModuleType, SimpleNamespace
import sys

import pytest

from coderag.maintenance import reset_service


def test_reset_bm25_storage_clears_memory_and_snapshots(
    patch_module_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Limpia el índice BM25 en memoria y recrea la carpeta de snapshots."""
    import coderag.ingestion.index_bm25 as bm25_module

    settings = patch_module_settings(bm25_module)
    repo_id = "repo-1"
    reset_service.GLOBAL_BM25.build(
        repo_id=repo_id,
        docs=["alpha beta"],
        metadatas=[{"id": "repo-1:1"}],
    )
    assert reset_service.GLOBAL_BM25.persist_repo(repo_id) is True

    cleared, warnings = reset_service._reset_bm25_storage(settings)

    assert warnings == []
    assert cleared[0] == "BM25 en memoria"
    assert cleared[1].startswith("BM25 snapshots (")
    assert reset_service.GLOBAL_BM25.has_repo(repo_id) is False
    assert reset_service.GLOBAL_BM25.has_repo_snapshot(repo_id) is False
    assert (settings.workspace_path.parent / "bm25").exists()


def test_reset_postgres_lexical_storage_skips_when_no_dsn(
    make_test_settings,
) -> None:
    """No intenta limpiar LexicalStore cuando Postgres no está configurado."""
    settings = make_test_settings()

    cleared, warnings = reset_service._reset_postgres_lexical_storage(settings)

    assert cleared == []
    assert warnings == []


def test_delete_repo_bm25_storage_returns_counts(
    patch_module_settings,
) -> None:
    """Expone conteos de documentos y snapshots al borrar un repo BM25."""
    import coderag.ingestion.index_bm25 as bm25_module

    settings = patch_module_settings(bm25_module)
    repo_id = "repo-1"
    reset_service.GLOBAL_BM25.build(
        repo_id=repo_id,
        docs=["alpha beta", "gamma delta"],
        metadatas=[{"id": "repo-1:1"}, {"id": "repo-1:2"}],
    )
    assert reset_service.GLOBAL_BM25.persist_repo(repo_id) is True

    cleared, warnings, counts = reset_service._delete_repo_bm25_storage(repo_id)

    assert warnings == []
    assert cleared == ["BM25"]
    assert counts == {"bm25_docs": 2, "bm25_snapshots": 1}
    assert reset_service.GLOBAL_BM25.has_repo(repo_id) is False
    assert reset_service.GLOBAL_BM25.has_repo_snapshot(repo_id) is False
    assert settings.workspace_path.parent.exists()


def test_delete_repo_postgres_lexical_storage_uses_lexical_store(
    make_test_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Borra el corpus léxico Postgres con el lenguaje configurado."""

    calls: list[tuple[str, str, str]] = []

    class FakeLexicalStore:
        def __init__(self, dsn: str, language: str) -> None:
            calls.append(("init", dsn, language))

        def delete_repo(self, repo_id: str) -> dict[str, int]:
            calls.append(("delete_repo", repo_id, ""))
            return {"docs_removed": 3}

    fake_module = ModuleType("coderag.storage.lexical_store")
    fake_module.LexicalStore = FakeLexicalStore
    monkeypatch.setitem(sys.modules, "coderag.storage.lexical_store", fake_module)

    settings = make_test_settings(
        lexical_fts_language="spanish",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    cleared, warnings, counts = reset_service._delete_repo_postgres_lexical_storage(
        settings,
        "repo-1",
    )

    assert warnings == []
    assert cleared == ["LexicalStore"]
    assert counts == {"lexical_docs": 3}
    assert calls == [
        ("init", "postgresql://fake/db", "spanish"),
        ("delete_repo", "repo-1", ""),
    ]


def test_reset_all_storage_uses_vector_helper(
    patch_module_settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delega la limpieza global de Chroma al helper vectorial compartido."""
    settings = patch_module_settings(
        reset_service,
        workspace_path=tmp_path / "workspace",
        chroma_path=tmp_path / "chroma",
        chroma_mode="local",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(reset_service, "_reset_bm25_storage", lambda settings: ([], []))
    monkeypatch.setattr(
        reset_service,
        "_reset_postgres_lexical_storage",
        lambda settings: ([], []),
    )
    monkeypatch.setattr(reset_service, "_remove_path", lambda path: None)
    monkeypatch.setattr(
        reset_service,
        "reset_managed_vector_storage",
        lambda active_settings, remove_path: (
            captured.update({"settings": active_settings, "remove_path": remove_path})
            or (True, ["warning vectorial"])
        ),
    )

    class FakeGraphSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, query: str) -> None:
            return None

    class FakeGraphDriver:
        def session(self) -> FakeGraphSession:
            return FakeGraphSession()

    class FakeGraphBuilder:
        def __init__(self) -> None:
            self.driver = FakeGraphDriver()

        def close(self) -> None:
            return None

    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)

    cleared, warnings = reset_service.reset_all_storage()

    assert "Chroma" in cleared
    assert "Grafo Neo4j" in cleared
    assert warnings == ["warning vectorial"]
    assert captured["settings"] is settings
    assert callable(captured["remove_path"])


def test_delete_repo_storage_uses_vector_helper(
    patch_module_settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delega el borrado vectorial por repo al helper compartido."""
    patch_module_settings(
        reset_service,
        workspace_path=tmp_path / "workspace",
        chroma_path=tmp_path / "chroma",
        chroma_mode="local",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        reset_service,
        "build_managed_vector_index",
        lambda: "fake-index",
    )
    monkeypatch.setattr(
        reset_service,
        "delete_repository_vector_documents",
        lambda index, repo_id: (
            captured.update({"index": index, "repo_id": repo_id})
            or {"total": 5, "code_symbols": 3, "code_files": 2}
        ),
    )
    monkeypatch.setattr(
        reset_service,
        "_delete_repo_bm25_storage",
        lambda repo_id: ([], [], {}),
    )
    monkeypatch.setattr(
        reset_service,
        "_delete_repo_postgres_lexical_storage",
        lambda settings, repo_id: ([], [], {}),
    )

    class FakeGraphBuilder:
        def delete_repo_subgraph(self, repo_id: str) -> int:
            return 0

        def close(self) -> None:
            return None

    class FakeMetadataStore:
        def delete_repo_data(self, repo_id: str) -> dict[str, int]:
            return {"jobs_deleted": 0, "repos_deleted": 0, "total": 0}

    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)
    monkeypatch.setattr(reset_service, "_workspace_repo_paths", lambda root, repo_id: [])
    monkeypatch.setattr(reset_service, "_build_metadata_store", lambda settings: FakeMetadataStore())
    monkeypatch.setattr(reset_service, "metadata_backend_label", lambda settings: "Metadata SQLite")

    cleared, warnings, counts = reset_service.delete_repo_storage("repo-1")

    assert warnings == []
    assert "Chroma" in cleared
    assert captured == {"index": "fake-index", "repo_id": "repo-1"}
    assert counts["chroma_total"] == 5
    assert counts["chroma_code_symbols"] == 3
    assert counts["chroma_code_files"] == 2