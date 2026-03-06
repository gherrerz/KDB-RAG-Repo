"""Pruebas de comportamiento de procesamiento por lotes del índice Chroma."""

from typing import Any

import pytest
from chromadb.errors import InvalidDimensionException

from coderag.ingestion.index_chroma import ChromaIndex


class _FakeCollection:
    """Colección Chroma falsa para pruebas unitarias de llamadas upsert."""

    def __init__(self, fail_once: bool = False) -> None:
        """Inicialice el estado de colección falsa."""
        self.calls: list[int] = []
        self.fail_once = fail_once

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
        self.calls.append(len(ids))

    def query(self, **kwargs: Any) -> dict[str, list[list[Any]]]:
        """Proporcione una respuesta de consulta mínima para que esté completa."""
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


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
    index = ChromaIndex()

    ids = [f"id{i}" for i in range(7)]
    docs = ["x"] * 7
    embeds = [[0.1, 0.2]] * 7
    metas = [{"i": i} for i in range(7)]
    index.upsert("code_symbols", ids, docs, embeds, metas)

    calls = fake_client.collections["code_symbols"].calls
    assert calls == [3, 3, 1]
