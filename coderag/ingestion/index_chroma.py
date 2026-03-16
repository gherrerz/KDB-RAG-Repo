"""Contenedor ChromaDB para indexación y búsqueda de vectores."""

from threading import Lock
from typing import Any
import gc

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


def _is_dimension_mismatch_error(exc: Exception) -> bool:
    """Detecte errores de dimensión de embeddings aún sin clase específica."""
    if isinstance(exc, InvalidDimensionException):
        return True
    message = str(exc).lower()
    return "embedding dimension" in message and "collection" in message


def _is_missing_collection_error(exc: Exception) -> bool:
    """Detecta errores de colección no encontrada emitidos por Chroma."""
    message = str(exc).lower()
    return "collection" in message and "does not exist" in message


class ChromaIndex:
    """Abstracción sobre colecciones persistentes de Chroma."""

    _shared_client: Any | None = None
    _shared_collections: dict[str, Any] | None = None
    _shared_path: str | None = None
    _shared_lock: Lock = Lock()

    @classmethod
    def reset_shared_state(cls) -> None:
        """Libera el cliente/colecciones compartidas para forzar reconstrucción limpia."""
        with cls._shared_lock:
            cls._shared_client = None
            cls._shared_collections = None
            cls._shared_path = None
        gc.collect()

    def __init__(self) -> None:
        """Inicialice el cliente y las colecciones persistentes de Chroma."""
        settings = get_settings()
        chroma_path = str(settings.chroma_path)
        with self._shared_lock:
            if (
                self._shared_client is None
                or self._shared_collections is None
                or self._shared_path != chroma_path
            ):
                client = chromadb.PersistentClient(
                    path=chroma_path,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                collections = {
                    name: client.get_or_create_collection(name)
                    for name in COLLECTIONS
                }
                self.__class__._shared_client = client
                self.__class__._shared_collections = collections
                self.__class__._shared_path = chroma_path

            self.client = self._shared_client
            self.collections = self._shared_collections

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
        except Exception as exc:
            if _is_missing_collection_error(exc):
                # Recupera referencias stale tras reset concurrente/externo.
                self.__class__.reset_shared_state()
                self.__init__()
                self._upsert_batched(
                    collection_name=collection_name,
                    ids=ids,
                    documents=documents,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    batch_size=batch_size,
                )
                return
            if not _is_dimension_mismatch_error(exc):
                raise
            raise RuntimeError(
                "Dimensión de embeddings incompatible con la colección "
                f"'{collection_name}'. Ajusta el modelo o limpia índices de "
                "forma controlada antes de reintentar."
            ) from exc

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
        except Exception as exc:
            if _is_missing_collection_error(exc):
                self.__class__.reset_shared_state()
                self.__init__()
                return self.collections[collection_name].query(
                    query_embeddings=[query_embedding],
                    n_results=top_n,
                    where=where,
                )
            if not _is_dimension_mismatch_error(exc):
                raise
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
