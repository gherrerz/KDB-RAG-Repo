"""ChromaDB wrapper for vector indexing and lookup."""

from typing import Any

import chromadb
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
    """Abstraction over Chroma persistent collections."""

    def __init__(self) -> None:
        """Initialize persistent Chroma client and collections."""
        settings = get_settings()
        self.client = chromadb.PersistentClient(path=str(settings.chroma_path))
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
        """Insert or update vectors and metadata in collection."""
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
        """Return safe maximum batch size supported by Chroma runtime."""
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
        """Upsert records in chunks to avoid Chroma batch limits."""
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
        """Search vectors by similarity and optional metadata filter."""
        try:
            return self.collections[collection_name].query(
                query_embeddings=[query_embedding],
                n_results=top_n,
                where=where,
            )
        except InvalidDimensionException:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
