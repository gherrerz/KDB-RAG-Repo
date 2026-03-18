"""Pruebas de comportamiento de procesamiento por lotes del índice Chroma."""

from typing import Any

import pytest
from chromadb.errors import InvalidDimensionException

from coderag.ingestion.index_chroma import ChromaIndex


class _FakeCollection:
    """Colección Chroma falsa para pruebas unitarias de llamadas upsert."""

    def __init__(
        self,
        fail_once: bool = False,
        error_once: Exception | None = None,
    ) -> None:
        """Inicialice el estado de colección falsa."""
        self.calls: list[int] = []
        self.fail_once = fail_once
        self.error_once = error_once
        self.repo_ids: list[str] = []

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Registre el tamaño de la llamada y, opcionalmente, simule el error de la primera llamada."""
        if self.fail_once:
            self.fail_once = False
            raise InvalidDimensionException("dim")
        if self.error_once is not None:
            error = self.error_once
            self.error_once = None
            raise error
        self.calls.append(len(ids))

    def query(self, **kwargs: Any) -> dict[str, list[list[Any]]]:
        """Proporcione una respuesta de consulta mínima para que esté completa."""
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def get(self, **kwargs: Any) -> dict[str, list[str]]:
        """Devuelve ids filtrados por repo_id para probar borrado selectivo."""
        where = kwargs.get("where") or {}
        repo_id = where.get("repo_id")
        if repo_id is None:
            return {"ids": []}

        matches = [item_id for item_id in self.repo_ids if item_id.startswith(f"{repo_id}:")]
        limit = int(kwargs.get("limit") or len(matches))
        return {"ids": matches[:limit]}

    def delete(self, ids: list[str]) -> None:
        """Elimina ids simulados para un repo_id en pruebas."""
        self.repo_ids = [item_id for item_id in self.repo_ids if item_id not in set(ids)]


class _FakeClient:
    """Cliente Chroma falso con tamaño de lote configurable."""

    def __init__(self) -> None:
        """Inicializar mapa de colecciones para cliente falso."""
        self.collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, name: str) -> _FakeCollection:
        """Devuelve o crea una colección falsa por nombre."""
        collection = self.collections.get(name)
        if collection is None:
            collection = _FakeCollection()
            self.collections[name] = collection
        return collection

    def delete_collection(self, name: str) -> None:
        """Elimina una colección falsa."""
        if name in self.collections:
            del self.collections[name]

    def get_max_batch_size(self) -> int:
        """Devuelve un tamaño de lote máximo falso estricto para afirmaciones de prueba."""
        return 3


def test_upsert_is_split_by_chroma_max_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Divide los upserts en múltiples llamadas que respetan el tamaño máximo de lote."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    module.ChromaIndex._shared_client = None
    module.ChromaIndex._shared_collections = None
    module.ChromaIndex._shared_path = None
    index = ChromaIndex()

    ids = [f"id{i}" for i in range(7)]
    docs = ["x"] * 7
    embeds = [[0.1, 0.2]] * 7
    metas = [{"i": i} for i in range(7)]
    index.upsert("code_symbols", ids, docs, embeds, metas)

    calls = fake_client.collections["code_symbols"].calls
    assert calls == [3, 3, 1]


def test_upsert_recovers_from_dimension_message_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lanza error controlado sin borrar la colección ante mismatch dimensional."""
    fake_client = _FakeClient()
    fake_client.collections["code_symbols"] = _FakeCollection(
        error_once=RuntimeError(
            "Embedding dimension 256 does not match collection dimensionality 1536"
        )
    )

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    module.ChromaIndex._shared_client = None
    module.ChromaIndex._shared_collections = None
    module.ChromaIndex._shared_path = None
    index = ChromaIndex()

    ids = ["id1", "id2"]
    docs = ["x", "y"]
    embeds = [[0.1, 0.2], [0.2, 0.1]]
    metas = [{"i": 1}, {"i": 2}]
    with pytest.raises(RuntimeError) as exc_info:
        index.upsert("code_symbols", ids, docs, embeds, metas)

    message = str(exc_info.value)
    assert "Dimensión de embeddings incompatible" in message


def test_delete_by_repo_id_removes_documents_from_all_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Elimina documentos filtrados por repo_id y devuelve conteo total agregado."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    module.ChromaIndex._shared_client = None
    module.ChromaIndex._shared_collections = None
    module.ChromaIndex._shared_path = None
    index = ChromaIndex()

    for collection in fake_client.collections.values():
        collection.repo_ids = ["r1:a", "r1:b", "other:c"]

    result = index.delete_by_repo_id("r1")

    assert result["total"] == 10
    assert result["code_symbols"] == 2
    assert result["code_files"] == 2
    assert result["code_modules"] == 2
    assert result["docs_misc"] == 2
    assert result["infra_ci"] == 2
    for collection in fake_client.collections.values():
        assert collection.repo_ids == ["other:c"]
