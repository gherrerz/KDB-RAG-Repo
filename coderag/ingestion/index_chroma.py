"""Contenedor ChromaDB para indexación y búsqueda de vectores."""

from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import InvalidDimensionException

from coderag.core.settings import get_settings

COLLECTIONS = [
    "code_symbols",
    "code_files",
    "code_modules",
    "docs_misc",
    "infra_ci",
]


class ChromaIndex:
    """Abstracción sobre colecciones persistentes de Chroma."""

    def __init__(self) -> None:
        """Inicialice el cliente y las colecciones persistentes de Chroma."""
        settings = get_settings()
        self.client = chromadb.PersistentClient(
            path=str(settings.chroma_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collections = {
            name: self.client.get_or_create_collection(name)
            for name in COLLECTIONS
        }

    def upsert(
        self,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Insertar o actualizar vectores y metadatos en la colección."""
        batch_size = self._max_batch_size()
        try:
            self._upsert_batched(
                collection_name=collection_name,
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
                batch_size=batch_size,
            )
        except InvalidDimensionException:
            self.client.delete_collection(collection_name)
            recreated = self.client.get_or_create_collection(collection_name)
            self.collections[collection_name] = recreated
            self._upsert_batched(
                collection_name=collection_name,
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
                batch_size=batch_size,
            )

    def _max_batch_size(self) -> int:
        """Devuelve el tamaño de lote máximo seguro admitido por el tiempo de ejecución de Chroma."""
        getter = getattr(self.client, "get_max_batch_size", None)
        if callable(getter):
            value = getter()
            if isinstance(value, int) and value > 0:
                return value
        return 5000

    def _upsert_batched(
        self,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        batch_size: int,
    ) -> None:
        """Realiza upsert por lotes para evitar límites de tamaño en Chroma."""
        for index in range(0, len(ids), batch_size):
            end = index + batch_size
            self.collections[collection_name].upsert(
                ids=ids[index:end],
                documents=documents[index:end],
                embeddings=embeddings[index:end],
                metadatas=metadatas[index:end],
            )

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_n: int,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Busque vectores por similitud y filtro de metadatos opcional."""
        try:
            return self.collections[collection_name].query(
                query_embeddings=[query_embedding],
                n_results=top_n,
                where=where,
            )
        except InvalidDimensionException:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
