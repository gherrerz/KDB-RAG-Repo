"""Contratos y helpers operativos para el backend vectorial."""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import chromadb
from chromadb.config import Settings as ChromaSettings

from coderag.ingestion.index_chroma import (
    COLLECTIONS,
    ChromaIndex,
    build_remote_chroma_error_message,
    build_remote_chroma_client,
)


@runtime_checkable
class ManagedVectorIndex(Protocol):
    """Contrato mínimo para operaciones vectoriales de mantenimiento/salud."""

    client: Any
    collections: dict[str, Any]

    def collection_hnsw_spaces(self) -> dict[str, str | None]:
        """Retorna el espacio HNSW detectado por colección."""

    def count_by_repo_id(
        self,
        collection_name: str,
        repo_id: str,
        page_size: int = 500,
    ) -> int:
        """Cuenta documentos de un repositorio en una colección gestionada."""

    def delete_by_repo_id(self, repo_id: str) -> dict[str, int]:
        """Elimina documentos de un repositorio en todas las colecciones."""


def build_managed_vector_index() -> ManagedVectorIndex:
    """Construye el backend vectorial operativo activo."""
    return ChromaIndex()


def count_repository_vector_documents(
    index: ManagedVectorIndex,
    *,
    repo_id: str,
    collection_names: tuple[str, ...] | list[str],
) -> int:
    """Suma documentos del repositorio a través de varias colecciones."""
    total = 0
    for collection_name in collection_names:
        total += index.count_by_repo_id(
            collection_name=collection_name,
            repo_id=repo_id,
        )
    return total


def count_repository_vector_collection_documents(
    index: ManagedVectorIndex,
    *,
    repo_id: str,
    collection_name: str,
    page_size: int = 500,
) -> int:
    """Cuenta documentos del repositorio en una colección concreta."""
    return index.count_by_repo_id(
        collection_name=collection_name,
        repo_id=repo_id,
        page_size=page_size,
    )


def managed_vector_collection_spaces(
    index: ManagedVectorIndex,
) -> dict[str, str | None]:
    """Expone el mapa de espacios HNSW del backend vectorial activo."""
    return index.collection_hnsw_spaces()


def delete_repository_vector_documents(
    index: ManagedVectorIndex,
    repo_id: str,
) -> dict[str, int]:
    """Elimina datos vectoriales del repositorio en el backend activo."""
    return index.delete_by_repo_id(repo_id)


def reset_managed_vector_storage(
    settings: object,
    *,
    remove_path: Callable[[Path], None] | None = None,
) -> tuple[bool, list[str]]:
    """Limpia las colecciones vectoriales gestionadas y storage local si aplica."""
    warnings: list[str] = []
    client: Any | None = None
    chroma_mode = getattr(settings, "chroma_mode", "local")
    reset_done = False

    ChromaIndex.reset_shared_state()

    try:
        if chroma_mode == "remote":
            client = build_remote_chroma_client(settings)
        else:
            client = chromadb.PersistentClient(
                path=str(settings.chroma_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )

        for collection_name in COLLECTIONS:
            try:
                client.delete_collection(collection_name)
            except Exception as exc:
                lowered = str(exc).lower()
                if "collection" in lowered and "does not exist" in lowered:
                    continue
                if chroma_mode == "remote":
                    raise RuntimeError(
                        build_remote_chroma_error_message(
                            settings,
                            operation="eliminar colección",
                            exc=exc,
                            collection_name=collection_name,
                        )
                    ) from exc
                continue

        reset_done = True
    except Exception as exc:
        if chroma_mode == "remote":
            warnings.append(str(exc))
        else:
            warnings.append(
                f"No se pudieron limpiar colecciones Chroma por API: {exc}"
            )
    finally:
        try:
            del client
        except Exception:
            pass
        gc.collect()

    if chroma_mode != "remote":
        ChromaIndex.reset_shared_state()
        if remove_path is not None:
            try:
                remove_path(settings.chroma_path)
            except RuntimeError as exc:
                warnings.append(
                    "No se pudo vaciar carpeta Chroma por lock de archivos: "
                    f"{exc}"
                )
        settings.chroma_path.mkdir(parents=True, exist_ok=True)

    ChromaIndex.reset_shared_state()
    return reset_done, warnings