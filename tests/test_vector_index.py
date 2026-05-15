"""Pruebas unitarias para helpers operativos del backend vectorial."""

from pathlib import Path
from types import SimpleNamespace

from coderag.core.vector_index import (
    ManagedVectorIndex,
    build_managed_vector_index,
    count_repository_vector_collection_documents,
    count_repository_vector_documents,
    delete_repository_vector_documents,
    managed_vector_collection_spaces,
    reset_managed_vector_storage,
)
from coderag.ingestion.index_chroma import ChromaIndex, COLLECTIONS


def test_build_managed_vector_index_returns_chroma_index(monkeypatch) -> None:
    """La factory vectorial retorna el backend Chroma activo."""

    class FakeChromaIndex:
        client = object()
        collections = {}

        def collection_hnsw_spaces(self) -> dict[str, str | None]:
            return {}

        def count_by_repo_id(self, collection_name: str, repo_id: str) -> int:
            del collection_name, repo_id
            return 0

        def delete_by_repo_id(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"total": 0}

    monkeypatch.setattr(
        "coderag.core.vector_index.ChromaIndex",
        FakeChromaIndex,
    )

    index = build_managed_vector_index()

    assert isinstance(index, FakeChromaIndex)
    assert isinstance(index, ManagedVectorIndex)


def test_count_repository_vector_documents_sums_managed_collections() -> None:
    """Suma conteos por colección usando el contrato vectorial mínimo."""

    class FakeIndex:
        client = object()
        collections = {}

        def collection_hnsw_spaces(self) -> dict[str, str | None]:
            return {}

        def count_by_repo_id(self, collection_name: str, repo_id: str) -> int:
            del repo_id
            return {"code_symbols": 2, "code_files": 3}.get(collection_name, 0)

        def delete_by_repo_id(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"total": 0}

    total = count_repository_vector_documents(
        FakeIndex(),
        repo_id="repo-1",
        collection_names=("code_symbols", "code_files"),
    )

    assert total == 5


def test_count_repository_vector_collection_documents_delegates_page_size() -> None:
    """Delega el conteo por colección preservando el page_size solicitado."""

    class FakeIndex:
        client = object()
        collections = {"code_symbols": object()}

        def collection_hnsw_spaces(self) -> dict[str, str | None]:
            return {}

        def count_by_repo_id(
            self,
            collection_name: str,
            repo_id: str,
            page_size: int = 500,
        ) -> int:
            assert collection_name == "code_symbols"
            assert repo_id == "repo-1"
            assert page_size == 250
            return 7

        def delete_by_repo_id(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"total": 0}

    total = count_repository_vector_collection_documents(
        FakeIndex(),
        repo_id="repo-1",
        collection_name="code_symbols",
        page_size=250,
    )

    assert total == 7


def test_managed_vector_helpers_delegate_to_backend() -> None:
    """Delegan espacios HNSW y borrado al backend vectorial activo."""

    class FakeIndex:
        client = object()
        collections = {}

        def collection_hnsw_spaces(self) -> dict[str, str | None]:
            return {"code_symbols": "cosine"}

        def count_by_repo_id(self, collection_name: str, repo_id: str) -> int:
            del collection_name, repo_id
            return 0

        def delete_by_repo_id(self, repo_id: str) -> dict[str, int]:
            assert repo_id == "repo-1"
            return {"total": 4}

    index = FakeIndex()

    assert managed_vector_collection_spaces(index) == {"code_symbols": "cosine"}
    assert delete_repository_vector_documents(index, "repo-1") == {"total": 4}


def test_reset_managed_vector_storage_remote_deletes_managed_collections(
    monkeypatch,
) -> None:
    """En modo remoto limpia colecciones gestionadas sin tocar storage local."""

    deleted: list[str] = []

    class FakeRemoteClient:
        def delete_collection(self, collection_name: str) -> None:
            deleted.append(collection_name)

    monkeypatch.setattr(
        "coderag.core.vector_index.build_remote_chroma_client",
        lambda settings: FakeRemoteClient(),
    )
    monkeypatch.setattr(
        ChromaIndex,
        "reset_shared_state",
        classmethod(lambda cls: None),
    )

    settings = SimpleNamespace(chroma_mode="remote")

    reset_done, warnings = reset_managed_vector_storage(settings)

    assert reset_done is True
    assert warnings == []
    assert deleted == COLLECTIONS


def test_reset_managed_vector_storage_remote_returns_sanitized_warning(
    monkeypatch,
) -> None:
    """En modo remoto retorna warning con destino y auth mode cuando falla reset."""

    class FailingRemoteClient:
        def delete_collection(self, collection_name: str) -> None:
            del collection_name
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(
        "coderag.core.vector_index.build_remote_chroma_client",
        lambda settings: FailingRemoteClient(),
    )
    monkeypatch.setattr(
        ChromaIndex,
        "reset_shared_state",
        classmethod(lambda cls: None),
    )

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="svc-user",
        chroma_password="svc-pass",
    )

    reset_done, warnings = reset_managed_vector_storage(settings)

    assert reset_done is False
    assert len(warnings) == 1
    assert "eliminar colección" in warnings[0]
    assert "chroma.example.local:8443" in warnings[0]
    assert "auth=basic" in warnings[0]
    assert "colección=code_symbols" in warnings[0]
    assert "svc-pass" not in warnings[0]


def test_reset_managed_vector_storage_local_recreates_embedded_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """En modo local limpia colecciones y recrea la carpeta persistente."""

    deleted: list[str] = []
    removed: list[Path] = []

    class FakeLocalClient:
        def delete_collection(self, collection_name: str) -> None:
            deleted.append(collection_name)

    monkeypatch.setattr(
        "coderag.core.vector_index.chromadb.PersistentClient",
        lambda path, settings: FakeLocalClient(),
    )
    monkeypatch.setattr(
        ChromaIndex,
        "reset_shared_state",
        classmethod(lambda cls: None),
    )

    chroma_path = tmp_path / "chroma"
    settings = SimpleNamespace(chroma_mode="local", chroma_path=chroma_path)

    reset_done, warnings = reset_managed_vector_storage(
        settings,
        remove_path=lambda path: removed.append(path),
    )

    assert reset_done is True
    assert warnings == []
    assert deleted == COLLECTIONS
    assert removed == [chroma_path]
    assert chroma_path.exists()