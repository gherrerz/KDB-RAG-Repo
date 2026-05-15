"""Contenedor ChromaDB para indexación y búsqueda de vectores."""

import base64
import hashlib
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
CHROMA_HNSW_SPACES = {"l2", "cosine"}


def _build_remote_auth_header(settings: Any) -> str | None:
    """Resuelve el header Authorization para Chroma remoto."""
    token = str(getattr(settings, "chroma_token", "") or "").strip()
    if token:
        return f"Bearer {token}"

    username = str(getattr(settings, "chroma_username", "") or "").strip()
    password = str(getattr(settings, "chroma_password", "") or "").strip()
    if not username or not password:
        return None

    encoded = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    return f"Basic {encoded}"


def build_remote_chroma_headers(settings: Any) -> dict[str, str]:
    """Construye headers opcionales para un cliente remoto de Chroma."""
    auth_header = _build_remote_auth_header(settings)
    if not auth_header:
        return {}
    return {"Authorization": auth_header}


def describe_remote_chroma_target(settings: Any) -> str:
    """Resume el destino remoto de Chroma sin exponer secretos."""
    host = str(getattr(settings, "chroma_host", "") or "").strip() or "<unknown-host>"
    port = int(getattr(settings, "chroma_port", 8000) or 8000)
    return f"{host}:{port}"


def describe_remote_chroma_auth_mode(settings: Any) -> str:
    """Resume el modo de autenticación efectiva del cliente remoto."""
    token = str(getattr(settings, "chroma_token", "") or "").strip()
    if token:
        return "bearer"

    username = str(getattr(settings, "chroma_username", "") or "").strip()
    password = str(getattr(settings, "chroma_password", "") or "").strip()
    if username and password:
        return "basic"
    return "none"


def build_remote_chroma_error_message(
    settings: Any,
    *,
    operation: str,
    exc: Exception,
    collection_name: str | None = None,
) -> str:
    """Construye un mensaje sanitario para errores de Chroma remoto."""
    target = describe_remote_chroma_target(settings)
    auth_mode = describe_remote_chroma_auth_mode(settings)
    collection_hint = (
        f", colección={collection_name}"
        if collection_name
        else ""
    )
    host = str(getattr(settings, "chroma_host", "") or "").strip()
    compose_hint = ""
    if host == "chroma":
        compose_hint = (
            " Si usas docker-compose, el host 'chroma' solo existe "
            "cuando el perfil 'remote' está activo."
        )
    return (
        "No se pudo completar la operación de Chroma remoto "
        f"'{operation}' en {target} "
        f"(auth={auth_mode}{collection_hint})."
        f"{compose_hint} Error original: {exc}"
    )


def build_remote_chroma_client(settings: Any | None = None) -> Any:
    """Crea un cliente HTTP de Chroma remoto con auth opcional."""
    runtime_settings = settings or get_settings()
    try:
        return chromadb.HttpClient(
            host=runtime_settings.chroma_host,
            port=runtime_settings.chroma_port,
            headers=build_remote_chroma_headers(runtime_settings),
        )
    except Exception as exc:
        raise RuntimeError(
            build_remote_chroma_error_message(
                runtime_settings,
                operation="crear cliente HTTP",
                exc=exc,
            )
        ) from exc


def _build_remote_client_key(settings: Any) -> str:
    """Genera una clave estable del cliente remoto incluyendo auth efectiva."""
    auth_header = _build_remote_auth_header(settings) or ""
    auth_fingerprint = hashlib.sha256(auth_header.encode("utf-8")).hexdigest()[:12]
    return (
        f"remote:{settings.chroma_host}:{settings.chroma_port}:"
        f"{auth_fingerprint}"
    )


def _is_dimension_mismatch_error(exc: Exception) -> bool:
    """Detecte errores de dimensión de embeddings aún sin clase específica."""
    if isinstance(exc, InvalidDimensionException):
        return True
    message = str(exc).lower()
    has_collection = "collection" in message
    has_dimension = "dimension" in message
    has_embedding = "embedding" in message
    return has_collection and has_dimension and has_embedding


def _is_space_mismatch_error(exc: Exception) -> bool:
    """Detecta errores de incompatibilidad del espacio HNSW en colecciones."""
    message = str(exc).lower()
    return "hnsw" in message and "space" in message and "collection" in message


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
        """Inicialice el cliente y las colecciones de Chroma (embedded o remoto)."""
        settings = get_settings()
        hnsw_space = settings.resolve_chroma_hnsw_space()
        chroma_mode = settings.chroma_mode

        if chroma_mode == "remote":
            client_key = _build_remote_client_key(settings)
        else:
            client_key = str(settings.chroma_path)

        with self._shared_lock:
            if (
                self._shared_client is None
                or self._shared_collections is None
                or self._shared_path != client_key
            ):
                if chroma_mode == "remote":
                    client = build_remote_chroma_client(settings)
                else:
                    client = chromadb.PersistentClient(
                        path=str(settings.chroma_path),
                        settings=ChromaSettings(anonymized_telemetry=False),
                    )
                collections: dict[str, Any] = {}
                for name in COLLECTIONS:
                    try:
                        collections[name] = client.get_or_create_collection(
                            name,
                            metadata={"hnsw:space": hnsw_space},
                        )
                    except Exception as exc:
                        if chroma_mode == "remote":
                            raise RuntimeError(
                                build_remote_chroma_error_message(
                                    settings,
                                    operation="abrir colección gestionada",
                                    exc=exc,
                                    collection_name=name,
                                )
                            ) from exc
                        raise
                self.__class__._shared_client = client
                self.__class__._shared_collections = collections
                self.__class__._shared_path = client_key

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
        settings = get_settings()
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
                try:
                    self._upsert_batched(
                        collection_name=collection_name,
                        ids=ids,
                        documents=documents,
                        embeddings=embeddings,
                        metadatas=metadatas,
                        batch_size=batch_size,
                    )
                except Exception as retry_exc:
                    if settings.chroma_mode == "remote":
                        raise RuntimeError(
                            build_remote_chroma_error_message(
                                settings,
                                operation="upsert",
                                exc=retry_exc,
                                collection_name=collection_name,
                            )
                        ) from retry_exc
                    raise
                return
            if not _is_dimension_mismatch_error(exc):
                if _is_space_mismatch_error(exc):
                    raise RuntimeError(
                        "Espacio HNSW incompatible en Chroma. Verifica "
                        "CHROMA_HNSW_SPACE y recrea índices antes de reintentar."
                    ) from exc
                if settings.chroma_mode == "remote":
                    raise RuntimeError(
                        build_remote_chroma_error_message(
                            settings,
                            operation="upsert",
                            exc=exc,
                            collection_name=collection_name,
                        )
                    ) from exc
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
        settings = get_settings()
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
                try:
                    return self.collections[collection_name].query(
                        query_embeddings=[query_embedding],
                        n_results=top_n,
                        where=where,
                    )
                except Exception as retry_exc:
                    if settings.chroma_mode == "remote":
                        raise RuntimeError(
                            build_remote_chroma_error_message(
                                settings,
                                operation="query",
                                exc=retry_exc,
                                collection_name=collection_name,
                            )
                        ) from retry_exc
                    raise
            if not _is_dimension_mismatch_error(exc):
                if _is_space_mismatch_error(exc):
                    raise RuntimeError(
                        "Espacio HNSW incompatible en Chroma. Verifica "
                        "CHROMA_HNSW_SPACE y recrea índices antes de consultar."
                    ) from exc
                if settings.chroma_mode == "remote":
                    raise RuntimeError(
                        build_remote_chroma_error_message(
                            settings,
                            operation="query",
                            exc=exc,
                            collection_name=collection_name,
                        )
                    ) from exc
                raise
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    @staticmethod
    def _normalize_hnsw_space(raw_value: Any) -> str | None:
        """Normaliza el valor del espacio HNSW almacenado en metadata."""
        normalized = str(raw_value or "").strip().lower()
        if normalized in CHROMA_HNSW_SPACES:
            return normalized
        return None

    def collection_hnsw_spaces(self) -> dict[str, str | None]:
        """Devuelve el espacio HNSW detectado por colección gestionada."""
        spaces: dict[str, str | None] = {}
        for name, collection in self.collections.items():
            metadata = getattr(collection, "metadata", None) or {}
            space = self._normalize_hnsw_space(metadata.get("hnsw:space"))
            if space is None:
                # Chroma usa l2 por defecto cuando no se define metadata explícita.
                space = "l2"
            spaces[name] = space
        return spaces

    def count_by_repo_id(
        self,
        collection_name: str,
        repo_id: str,
        page_size: int = 500,
    ) -> int:
        """Cuenta documentos de un repositorio en una colección Chroma."""
        settings = get_settings()
        collection = self.collections[collection_name]
        total = 0
        offset = 0
        while True:
            try:
                page = collection.get(
                    where={"repo_id": repo_id},
                    limit=page_size,
                    offset=offset,
                    include=[],
                )
            except Exception as exc:
                if settings.chroma_mode == "remote":
                    raise RuntimeError(
                        build_remote_chroma_error_message(
                            settings,
                            operation="contar documentos por repo_id",
                            exc=exc,
                            collection_name=collection_name,
                        )
                    ) from exc
                raise
            ids = page.get("ids") or []
            page_count = len(ids)
            total += page_count
            if page_count < page_size:
                break
            offset += page_size
        return total

    def delete_by_repo_id(
        self,
        repo_id: str,
    ) -> dict[str, int]:
        """Elimina documentos de todas las colecciones por repo_id y retorna conteos."""
        settings = get_settings()
        batch_size = self._max_batch_size()
        deleted_by_collection: dict[str, int] = {}

        for collection_name in COLLECTIONS:
            collection = self.collections[collection_name]
            deleted_total = 0

            while True:
                try:
                    page = collection.get(
                        where={"repo_id": repo_id},
                        limit=batch_size,
                        offset=0,
                        include=[],
                    )
                except Exception as exc:
                    if settings.chroma_mode == "remote":
                        raise RuntimeError(
                            build_remote_chroma_error_message(
                                settings,
                                operation="listar documentos para delete_by_repo_id",
                                exc=exc,
                                collection_name=collection_name,
                            )
                        ) from exc
                    raise
                ids = page.get("ids") or []
                if not ids:
                    break
                try:
                    collection.delete(ids=ids)
                except Exception as exc:
                    if settings.chroma_mode == "remote":
                        raise RuntimeError(
                            build_remote_chroma_error_message(
                                settings,
                                operation="delete_by_repo_id",
                                exc=exc,
                                collection_name=collection_name,
                            )
                        ) from exc
                    raise
                deleted_total += len(ids)

            deleted_by_collection[collection_name] = deleted_total

        deleted_by_collection["total"] = sum(deleted_by_collection.values())
        return deleted_by_collection
