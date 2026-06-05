"""Pruebas unitarias focalizadas para limpieza de storage."""

from types import ModuleType, SimpleNamespace
import sys

import pytest

from coderag.maintenance import reset_service


def test_reset_postgres_lexical_storage_skips_when_no_dsn(
    make_test_settings,
) -> None:
    """No intenta limpiar LexicalStore cuando Postgres no está configurado."""
    settings = make_test_settings()

    cleared, warnings = reset_service._reset_postgres_lexical_storage(settings)

    assert cleared == []
    assert warnings == []


def test_delete_repo_postgres_lexical_storage_uses_lexical_store(
    make_test_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Borra el corpus léxico Postgres con el lenguaje configurado."""

    calls: list[tuple[str, str, str]] = []

    class FakeLexicalStore:
        def __init__(
            self,
            dsn: str,
            language: str,
            session_factory=None,
        ) -> None:
            calls.append(("init", dsn, language))
            assert session_factory is not None

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
    monkeypatch.setattr(
        reset_service,
        "resolve_postgres_dsn",
        lambda active_settings: "postgresql://fake/db",
    )

    class FakeGraphBuilder:
        def clear_graph(self) -> int:
            return 0

        def close(self) -> None:
            return None

    class FakePostgresMetadataStore:
        def reset_all(self) -> None:
            return None

    fake_module = ModuleType("coderag.storage.postgres_metadata_store")
    fake_module.PostgresMetadataStore = lambda dsn: FakePostgresMetadataStore()
    monkeypatch.setitem(
        sys.modules,
        "coderag.storage.postgres_metadata_store",
        fake_module,
    )
    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)

    cleared, warnings = reset_service.reset_all_storage()

    assert "Chroma" in cleared
    assert "Grafo Neo4j" in cleared
    assert warnings == ["warning vectorial"]
    assert captured["settings"] is settings
    assert callable(captured["remove_path"])


def test_reset_all_storage_skips_bm25_cleanup_when_postgres_is_primary(
    make_test_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con Postgres activo, el reset no expone limpieza de artefactos legacy."""
    settings = make_test_settings(
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    monkeypatch.setattr(reset_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        reset_service,
        "_reset_postgres_lexical_storage",
        lambda active_settings: (["LexicalStore Postgres"], []),
    )
    monkeypatch.setattr(
        reset_service,
        "reset_managed_vector_storage",
        lambda active_settings, remove_path: (False, []),
    )
    monkeypatch.setattr(reset_service, "_remove_path", lambda path: None)

    class FakeGraphBuilder:
        def clear_graph(self) -> int:
            return 0

        def close(self) -> None:
            return None

    class FakePostgresMetadataStore:
        def reset_all(self) -> None:
            return None

    fake_module = ModuleType("coderag.storage.postgres_metadata_store")
    fake_module.PostgresMetadataStore = lambda dsn: FakePostgresMetadataStore()
    monkeypatch.setitem(
        sys.modules,
        "coderag.storage.postgres_metadata_store",
        fake_module,
    )
    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)

    cleared, warnings = reset_service.reset_all_storage()

    assert "LexicalStore Postgres" in cleared
    assert warnings == []


def test_reset_all_storage_warns_when_postgres_metadata_is_missing(
    make_test_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin Postgres configurado, el reset no debe volver a metadata.db."""
    settings = make_test_settings(resolve_postgres_dsn=lambda: "")

    monkeypatch.setattr(reset_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        reset_service,
        "_reset_postgres_lexical_storage",
        lambda active_settings: ([], []),
    )
    monkeypatch.setattr(
        reset_service,
        "reset_managed_vector_storage",
        lambda active_settings, remove_path: (False, []),
    )
    monkeypatch.setattr(reset_service, "_remove_path", lambda path: None)

    class FakeGraphBuilder:
        def clear_graph(self) -> int:
            return 0

        def close(self) -> None:
            return None

    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)

    cleared, warnings = reset_service.reset_all_storage()

    assert all("SQLite" not in item for item in cleared)
    assert warnings == [
        "Metadata Postgres no está configurado; no se limpió metadata operativa durante el reset."
    ]


def test_reset_all_storage_warns_when_neo4j_cleanup_fails(
    patch_module_settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convierte la caída de Neo4j en warning sanitario durante reset global."""
    patch_module_settings(
        reset_service,
        workspace_path=tmp_path / "workspace",
        chroma_path=tmp_path / "chroma",
        chroma_mode="local",
    )

    monkeypatch.setattr(
        reset_service,
        "_reset_postgres_lexical_storage",
        lambda settings: ([], []),
    )
    monkeypatch.setattr(reset_service, "_remove_path", lambda path: None)
    monkeypatch.setattr(
        reset_service,
        "reset_managed_vector_storage",
        lambda active_settings, remove_path: (True, []),
    )
    monkeypatch.setattr(
        reset_service,
        "resolve_postgres_dsn",
        lambda active_settings: "postgresql://fake/db",
    )

    class FakeGraphBuilder:
        def clear_graph(self) -> int:
            raise RuntimeError(
                "No se pudo completar la operación de Neo4j 'eliminar grafo "
                "completo' en neo4j:7687 (auth=basic). Error original: timeout"
            )

        def close(self) -> None:
            return None

    class FakePostgresMetadataStore:
        def reset_all(self) -> None:
            return None

    fake_module = ModuleType("coderag.storage.postgres_metadata_store")
    fake_module.PostgresMetadataStore = lambda dsn: FakePostgresMetadataStore()
    monkeypatch.setitem(
        sys.modules,
        "coderag.storage.postgres_metadata_store",
        fake_module,
    )
    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)

    cleared, warnings = reset_service.reset_all_storage()

    assert "Chroma" in cleared
    assert "Grafo Neo4j" not in cleared
    assert warnings == [
        "No se pudo limpiar Neo4j: No se pudo completar la operación de "
        "Neo4j 'eliminar grafo completo' en neo4j:7687 (auth=basic). "
        "Error original: timeout"
    ]


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
            return {
                "snapshots_deleted": 0,
                "jobs_deleted": 0,
                "repos_deleted": 0,
                "total": 0,
            }

    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)
    monkeypatch.setattr(reset_service, "_workspace_repo_paths", lambda root, repo_id: [])
    monkeypatch.setattr(reset_service, "_build_metadata_store", lambda settings: FakeMetadataStore())
    monkeypatch.setattr(
        reset_service,
        "metadata_backend_label",
        lambda settings: "Metadata Postgres",
    )

    cleared, warnings, counts = reset_service.delete_repo_storage("repo-1")

    assert warnings == []
    assert "Chroma" in cleared
    assert captured == {"index": "fake-index", "repo_id": "repo-1"}
    assert counts["chroma_total"] == 5
    assert counts["chroma_code_symbols"] == 3
    assert counts["chroma_code_files"] == 2
    assert counts["metadata_snapshots"] == 0


def test_delete_repo_storage_warns_when_metadata_backend_is_unavailable(
    make_test_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El borrado por repo no debe volver a metadata.db cuando falta Postgres."""
    settings = make_test_settings(resolve_postgres_dsn=lambda: "")

    monkeypatch.setattr(reset_service, "get_settings", lambda: settings)
    monkeypatch.setattr(reset_service, "build_managed_vector_index", lambda: "fake-index")
    monkeypatch.setattr(
        reset_service,
        "delete_repository_vector_documents",
        lambda index, repo_id: {"total": 1},
    )
    monkeypatch.setattr(
        reset_service,
        "_delete_repo_postgres_lexical_storage",
        lambda active_settings, repo_id: (["LexicalStore"], [], {"lexical_docs": 3}),
    )

    class FakeGraphBuilder:
        def delete_repo_subgraph(self, repo_id: str) -> int:
            return 0

        def close(self) -> None:
            return None

    monkeypatch.setattr(reset_service, "GraphBuilder", FakeGraphBuilder)
    monkeypatch.setattr(reset_service, "_workspace_repo_paths", lambda root, repo_id: [])
    monkeypatch.setattr(
        reset_service,
        "_build_metadata_store",
        lambda active_settings: (_ for _ in ()).throw(
            RuntimeError("Metadata Postgres es obligatorio en el runtime actual.")
        ),
    )

    cleared, warnings, counts = reset_service.delete_repo_storage("repo-1")

    assert "LexicalStore" in cleared
    assert "metadata_total" not in counts
    assert warnings == [
        "No se pudo limpiar metadata operativa para 'repo-1': Metadata Postgres es obligatorio en el runtime actual."
    ]