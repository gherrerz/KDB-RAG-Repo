"""Contenedor ChromaDB para indexación y búsqueda de vectores."""

import base64
import hashlib
import json
from threading import Lock
from typing import Any
import gc

import chromadb

# La imagen de servidor usa `chromadb-client` (thin) por CVE-2026-45829: garantiza HttpClient pero
# puede no exponer config.Settings / errors / PersistentClient. Importes defensivos para no romper
# la carga del módulo en modo remoto. El modo local requiere `chromadb` completo (ver _require_persistent_client).
try:
    from chromadb.config import Settings as ChromaSettings
except ImportError:  # pragma: no cover - solo con thin client
    ChromaSettings = None

try:
    from chromadb.errors import InvalidDimensionException
except ImportError:  # pragma: no cover - solo con thin client
    class InvalidDimensionException(Exception):
        """Placeholder cuando el thin client no expone la excepción nativa."""

from coderag.core.settings import get_settings

_LOCAL_CHROMA_UNAVAILABLE_MESSAGE = (
    "Modo local de Chroma no disponible: la imagen usa chromadb-client (solo remoto). "
    "Usa CHROMA_MODE=remote o instala el paquete `chromadb` completo."
)


def _require_persistent_client() -> Any:
    """Devuelve PersistentClient o falla con un mensaje claro si solo hay thin client."""
    persistent_client = getattr(chromadb, "PersistentClient", None)
    if persistent_client is None:
        raise RuntimeError(_LOCAL_CHROMA_UNAVAILABLE_MESSAGE)
    return persistent_client

COLLECTIONS = [
    "code_symbols",
    "code_files",
    "code_modules",
    "docs_misc",
    "infra_ci",
]
CHROMA_HNSW_SPACES = {"l2", "cosine"}
_REMOTE_CHROMA_ERROR_PREFIX = "No se pudo completar la operación de Chroma remoto"


def _chunked_sequence(items: list[str], size: int) -> list[list[str]]:
    """Parte una lista en sublistas de longitud máxima ``size``."""
    if size <= 0:
        return [list(items)]
    return [items[i : i + size] for i in range(0, len(items), size)]


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
    batch_size: int | None = None,
) -> str:
    """Construye un mensaje sanitario para errores de Chroma remoto."""
    target = describe_remote_chroma_target(settings)
    auth_mode = describe_remote_chroma_auth_mode(settings)
    details = [f"auth={auth_mode}"]
    if collection_name:
        details.append(f"colección={collection_name}")
    if batch_size is not None and batch_size > 0:
        details.append(f"lote={batch_size}")

    signal = _detect_remote_chroma_error_signal(exc)
    if signal is not None:
        details.append(f"señal={signal}")

    host = str(getattr(settings, "chroma_host", "") or "").strip()
    compose_hint = ""
    if host == "chroma":
        compose_hint = (
            " Si usas docker-compose, el host 'chroma' solo existe "
            "cuando el perfil 'remote' está activo."
        )
    remediation_hint = _build_remote_chroma_remediation_hint(signal)
    return (
        "No se pudo completar la operación de Chroma remoto "
        f"'{operation}' en {target} "
        f"({', '.join(details)})."
        f"{compose_hint}{remediation_hint} Error original: {exc}"
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


def _resolve_remote_batch_size_override(settings: Any) -> int | None:
    """Resuelve un override explícito de lote remoto sin afectar embedded."""
    if str(getattr(settings, "chroma_mode", "") or "").strip().lower() != "remote":
        return None

    raw_value = getattr(settings, "chroma_remote_batch_size_override", 0) or 0
    try:
        override = int(raw_value)
    except (TypeError, ValueError):
        return None
    if override <= 0:
        return None
    return override


def _resolve_remote_max_request_bytes(settings: Any) -> int:
    """Obtiene el presupuesto máximo estimado por request remoto."""
    raw_value = getattr(settings, "chroma_max_request_bytes", 50 * 1024 * 1024)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 50 * 1024 * 1024
    return max(1, value)


def _resolve_remote_min_batch_size(settings: Any) -> int:
    """Obtiene el tamaño mínimo de lote permitido para split retry."""
    raw_value = getattr(settings, "chroma_remote_min_batch_size", 25)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 25
    return max(1, value)


def _resolve_remote_max_split_depth(settings: Any) -> int:
    """Obtiene la profundidad máxima de split retry remoto."""
    raw_value = getattr(settings, "chroma_remote_max_split_depth", 6)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 6
    return max(1, value)


def _is_payload_too_large_error(exc: Exception) -> bool:
    """Detecta respuestas compatibles con payload demasiado grande."""
    message = str(exc).lower()
    return any(
        pattern in message
        for pattern in (
            "413",
            "payload too large",
            "request entity too large",
            "entity too large",
            "content length exceeded",
        )
    )


def _is_proxy_reset_error(exc: Exception) -> bool:
    """Detecta resets de conexión compatibles con proxy o service mesh."""
    message = str(exc).lower()
    return any(
        pattern in message
        for pattern in (
            "server disconnected without sending a response",
            "connection reset",
            "connection reset by peer",
            "disconnect/reset before headers",
            "remote protocol error",
            "broken pipe",
        )
    )


def _is_upstream_restarting_error(exc: Exception) -> bool:
    """Detecta señales compatibles con upstream no disponible o reiniciando."""
    message = str(exc).lower()
    return any(
        pattern in message
        for pattern in (
            "connection refused",
            "503",
            "service unavailable",
            "no healthy upstream",
            "connection aborted",
            "connection closed",
        )
    )


def _detect_remote_chroma_error_signal(exc: Exception) -> str | None:
    """Clasifica una señal operativa útil a partir de la excepción remota."""
    if _is_payload_too_large_error(exc):
        return "payload_grande"
    if _is_proxy_reset_error(exc):
        return "proxy_reset"
    if _is_upstream_restarting_error(exc):
        return "upstream_reiniciando"
    return None


def _build_remote_chroma_remediation_hint(signal: str | None) -> str:
    """Devuelve una pista operativa breve según la señal detectada."""
    if signal == "payload_grande":
        return (
            " Reduce CHROMA_REMOTE_BATCH_SIZE_OVERRIDE o ajusta "
            "CHROMA_MAX_REQUEST_BYTES y vuelve a intentar."
        )
    if signal == "proxy_reset":
        return (
            " Revisa resets o timeouts en proxy, ingress o service mesh "
            "entre la API y Chroma."
        )
    if signal == "upstream_reiniciando":
        return (
            " Revisa disponibilidad del servicio y posibles reinicios del "
            "pod remoto de Chroma."
        )
    return ""


def _estimate_upsert_request_bytes(
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict[str, Any]],
) -> int:
    """Estima el tamaño serializado del payload de un upsert remoto."""
    payload = {
        "ids": ids,
        "documents": documents,
        "embeddings": embeddings,
        "metadatas": metadatas,
    }
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _is_wrapped_remote_chroma_error(exc: Exception) -> bool:
    """Detecta errores remotos ya sanitizados para no duplicar wrapping."""
    return isinstance(exc, RuntimeError) and str(exc).startswith(
        _REMOTE_CHROMA_ERROR_PREFIX
    )


def _is_recoverable_remote_upsert_error(exc: Exception) -> bool:
    """Marca errores remotos recuperables mediante reducción de lote."""
    return _is_payload_too_large_error(exc) or _is_proxy_reset_error(exc)


def _build_upsert_metrics(
    *,
    collection_name: str,
    requested_batch_size: int,
) -> dict[str, int | str | None]:
    """Inicializa métricas operativas para una llamada de upsert."""
    return {
        "collection_name": collection_name,
        "requested_batch_size": max(1, requested_batch_size),
        "effective_batch_size": None,
        "split_count": 0,
        "recovered_retry_count": 0,
        "payload_too_large_events": 0,
        "proxy_reset_events": 0,
        "upstream_restarting_events": 0,
        "documents_written": 0,
    }


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
                    persistent_client = _require_persistent_client()
                    client = persistent_client(
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

    def list_collection_names(self) -> list[str]:
        """Lista los nombres de colecciones gestionadas por el backend."""
        return sorted(self.collections.keys())

    def get_collection_metadata(self, collection_name: str) -> dict[str, Any]:
        """Retorna una copia defensiva de la metadata de la colección."""
        metadata = getattr(self.collections[collection_name], "metadata", None) or {}
        return dict(metadata)

    def _count_collection_pages(
        self,
        collection: Any,
        *,
        page_size: int,
        where: dict[str, Any] | None = None,
    ) -> int:
        """Cuenta documentos vía paginación cuando no hay contador nativo."""
        total = 0
        offset = 0
        while True:
            page_args: dict[str, Any] = {
                "limit": page_size,
                "offset": offset,
                "include": [],
            }
            if where is not None:
                page_args["where"] = where
            page = collection.get(**page_args)
            ids = page.get("ids") or []
            page_count = len(ids)
            total += page_count
            if page_count < page_size:
                break
            offset += page_size
        return total

    def count_collection(
        self,
        collection_name: str,
        page_size: int = 500,
        where: dict[str, Any] | None = None,
    ) -> int:
        """Cuenta documentos totales en una colección gestionada."""
        settings = get_settings()
        collection = self.collections[collection_name]
        counter = getattr(collection, "count", None)
        safe_page_size = max(1, page_size)
        try:
            if callable(counter) and where is None:
                return int(counter())
            return self._count_collection_pages(
                collection,
                page_size=safe_page_size,
                where=where,
            )
        except Exception as exc:
            if settings.chroma_mode == "remote":
                raise RuntimeError(
                    build_remote_chroma_error_message(
                        settings,
                        operation="contar documentos de colección",
                        exc=exc,
                        collection_name=collection_name,
                        batch_size=safe_page_size,
                    )
                ) from exc
            raise

    def get_collection(
        self,
        collection_name: str,
        *,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
        include: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """Ejecuta una lectura directa tipo get sobre una colección."""
        settings = get_settings()
        request_args: dict[str, Any] = {}
        if where is not None:
            request_args["where"] = where
        if where_document is not None:
            request_args["where_document"] = where_document
        if include is not None:
            request_args["include"] = include
        if limit is not None:
            request_args["limit"] = limit
        if offset is not None:
            request_args["offset"] = offset
        try:
            return self.collections[collection_name].get(**request_args)
        except Exception as exc:
            if settings.chroma_mode == "remote":
                raise RuntimeError(
                    build_remote_chroma_error_message(
                        settings,
                        operation="leer colección con get",
                        exc=exc,
                        collection_name=collection_name,
                    )
                ) from exc
            raise

    def query_collection(
        self,
        collection_name: str,
        *,
        query_embeddings: list[list[float]] | None = None,
        query_texts: list[str] | None = None,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        """Ejecuta una consulta directa tipo query sobre una colección."""
        settings = get_settings()
        request_args: dict[str, Any] = {"n_results": n_results}
        if query_embeddings is not None:
            request_args["query_embeddings"] = query_embeddings
        if query_texts is not None:
            request_args["query_texts"] = query_texts
        if where is not None:
            request_args["where"] = where
        if where_document is not None:
            request_args["where_document"] = where_document
        if include is not None:
            request_args["include"] = include
        try:
            return self.collections[collection_name].query(**request_args)
        except Exception as exc:
            if _is_missing_collection_error(exc):
                self.__class__.reset_shared_state()
                self.__init__()
                try:
                    return self.collections[collection_name].query(**request_args)
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
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

    def upsert(
        self,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> dict[str, int | str | None]:
        """Insertar o actualizar vectores y metadatos en la colección."""
        settings = get_settings()
        batch_size = self._max_batch_size()
        metrics = _build_upsert_metrics(
            collection_name=collection_name,
            requested_batch_size=batch_size,
        )
        try:
            self._upsert_batched(
                collection_name=collection_name,
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
                batch_size=batch_size,
                metrics=metrics,
            )
        except Exception as exc:
            if _is_missing_collection_error(exc):
                # Recupera referencias stale tras reset concurrente/externo.
                self.__class__.reset_shared_state()
                self.__init__()
                metrics = _build_upsert_metrics(
                    collection_name=collection_name,
                    requested_batch_size=batch_size,
                )
                try:
                    self._upsert_batched(
                        collection_name=collection_name,
                        ids=ids,
                        documents=documents,
                        embeddings=embeddings,
                        metadatas=metadatas,
                        batch_size=batch_size,
                        metrics=metrics,
                    )
                except Exception as retry_exc:
                    if settings.chroma_mode == "remote":
                        if _is_wrapped_remote_chroma_error(retry_exc):
                            raise retry_exc
                        raise RuntimeError(
                            build_remote_chroma_error_message(
                                settings,
                                operation="upsert",
                                exc=retry_exc,
                                collection_name=collection_name,
                                batch_size=batch_size,
                            )
                        ) from retry_exc
                    raise
                return metrics
            if not _is_dimension_mismatch_error(exc):
                if _is_space_mismatch_error(exc):
                    raise RuntimeError(
                        "Espacio HNSW incompatible en Chroma. Verifica "
                        "CHROMA_HNSW_SPACE y recrea índices antes de reintentar."
                    ) from exc
                if settings.chroma_mode == "remote":
                    if _is_wrapped_remote_chroma_error(exc):
                        raise exc
                    raise RuntimeError(
                        build_remote_chroma_error_message(
                            settings,
                            operation="upsert",
                            exc=exc,
                            collection_name=collection_name,
                            batch_size=batch_size,
                        )
                    ) from exc
                raise
            raise RuntimeError(
                "Dimensión de embeddings incompatible con la colección "
                f"'{collection_name}'. Ajusta el modelo o limpia índices de "
                "forma controlada antes de reintentar."
            ) from exc
        return metrics

    def _resolve_effective_batch_size(
        self,
        settings: Any,
        *,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        start: int,
        batch_size: int,
    ) -> int:
        """Ajusta el lote inicial remoto según el presupuesto estimado."""
        if str(getattr(settings, "chroma_mode", "") or "").strip().lower() != "remote":
            return batch_size

        upper_bound = min(batch_size, len(ids) - start)
        if upper_bound <= 1:
            return upper_bound

        max_request_bytes = _resolve_remote_max_request_bytes(settings)

        def fits(candidate_size: int) -> bool:
            end = start + candidate_size
            estimated_size = _estimate_upsert_request_bytes(
                ids[start:end],
                documents[start:end],
                embeddings[start:end],
                metadatas[start:end],
            )
            return estimated_size <= max_request_bytes

        if fits(upper_bound):
            return upper_bound

        low = 1
        high = upper_bound
        best = 1
        while low <= high:
            mid = (low + high) // 2
            if fits(mid):
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    def _upsert_batch_with_retry(
        self,
        settings: Any,
        *,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        metrics: dict[str, int | str | None],
        split_depth: int = 0,
    ) -> None:
        """Intenta escribir un lote y lo subdivide si el fallo remoto es recuperable."""
        current_effective = int(metrics.get("effective_batch_size") or 0)
        if current_effective <= 0 or len(ids) < current_effective:
            metrics["effective_batch_size"] = len(ids)
        try:
            self.collections[collection_name].upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            metrics["documents_written"] = int(metrics["documents_written"] or 0) + len(ids)
            return
        except Exception as exc:
            signal = _detect_remote_chroma_error_signal(exc)
            if signal == "payload_grande":
                metrics["payload_too_large_events"] = (
                    int(metrics["payload_too_large_events"] or 0) + 1
                )
            elif signal == "proxy_reset":
                metrics["proxy_reset_events"] = (
                    int(metrics["proxy_reset_events"] or 0) + 1
                )
            elif signal == "upstream_reiniciando":
                metrics["upstream_restarting_events"] = (
                    int(metrics["upstream_restarting_events"] or 0) + 1
                )
            if (
                str(getattr(settings, "chroma_mode", "") or "").strip().lower()
                == "remote"
                and _is_recoverable_remote_upsert_error(exc)
                and len(ids) > _resolve_remote_min_batch_size(settings)
                and split_depth < _resolve_remote_max_split_depth(settings)
            ):
                metrics["split_count"] = int(metrics["split_count"] or 0) + 1
                metrics["recovered_retry_count"] = (
                    int(metrics["recovered_retry_count"] or 0) + 1
                )
                midpoint = max(1, len(ids) // 2)
                self._upsert_batch_with_retry(
                    settings,
                    collection_name=collection_name,
                    ids=ids[:midpoint],
                    documents=documents[:midpoint],
                    embeddings=embeddings[:midpoint],
                    metadatas=metadatas[:midpoint],
                    metrics=metrics,
                    split_depth=split_depth + 1,
                )
                self._upsert_batch_with_retry(
                    settings,
                    collection_name=collection_name,
                    ids=ids[midpoint:],
                    documents=documents[midpoint:],
                    embeddings=embeddings[midpoint:],
                    metadatas=metadatas[midpoint:],
                    metrics=metrics,
                    split_depth=split_depth + 1,
                )
                return

            if str(getattr(settings, "chroma_mode", "") or "").strip().lower() == "remote":
                raise RuntimeError(
                    build_remote_chroma_error_message(
                        settings,
                        operation="upsert",
                        exc=exc,
                        collection_name=collection_name,
                        batch_size=len(ids),
                    )
                ) from exc
            raise

    def _max_batch_size(self) -> int:
        """Devuelve el tamaño de lote máximo seguro admitido por el tiempo de ejecución de Chroma."""
        settings = get_settings()
        override = _resolve_remote_batch_size_override(settings)
        if override is not None:
            return override

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
        metrics: dict[str, int | str | None],
    ) -> None:
        """Realiza upsert por lotes para evitar límites de tamaño en Chroma."""
        settings = get_settings()
        index = 0
        while index < len(ids):
            effective_batch_size = self._resolve_effective_batch_size(
                settings,
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
                start=index,
                batch_size=batch_size,
            )
            end = index + effective_batch_size
            self._upsert_batch_with_retry(
                settings,
                collection_name=collection_name,
                ids=ids[index:end],
                documents=documents[index:end],
                embeddings=embeddings[index:end],
                metadatas=metadatas[index:end],
                metrics=metrics,
            )
            index = end

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_n: int,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Busque vectores por similitud y filtro de metadatos opcional."""
        return self.query_collection(
            collection_name,
            query_embeddings=[query_embedding],
            n_results=top_n,
            where=where,
        )

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
        while True:
            try:
                return self._count_collection_pages(
                    collection,
                    page_size=max(1, page_size),
                    where={"repo_id": repo_id},
                )
            except Exception as exc:
                if settings.chroma_mode == "remote":
                    raise RuntimeError(
                        build_remote_chroma_error_message(
                            settings,
                            operation="contar documentos por repo_id",
                            exc=exc,
                            collection_name=collection_name,
                            batch_size=max(1, page_size),
                        )
                    ) from exc
                raise

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
                                batch_size=batch_size,
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
                                batch_size=batch_size,
                            )
                        ) from exc
                    raise
                deleted_total += len(ids)

            deleted_by_collection[collection_name] = deleted_total

        deleted_by_collection["total"] = sum(deleted_by_collection.values())
        return deleted_by_collection

    def delete_by_repo_and_paths(
        self,
        repo_id: str,
        paths: list[str],
    ) -> dict[str, int]:
        """Elimina documentos del repo acotados a un set de paths y retorna conteos.

        Solo afecta documentos cuya metadata ``path`` coincide con los paths dados
        (símbolos y archivos). Los chunks de módulo se gestionan por separado en el
        pipeline porque su ``path`` referencia el nombre de módulo, no un archivo.
        """
        unique_paths = list(dict.fromkeys(p for p in paths if p))
        deleted_by_collection: dict[str, int] = {
            name: 0 for name in COLLECTIONS
        }
        if not unique_paths:
            deleted_by_collection["total"] = 0
            return deleted_by_collection

        settings = get_settings()
        batch_size = self._max_batch_size()

        for collection_name in COLLECTIONS:
            collection = self.collections[collection_name]
            deleted_total = 0
            # Acotar el tamaño de la cláusula $in para no inflar el request remoto.
            for path_batch in _chunked_sequence(unique_paths, 100):
                where_filter = {
                    "$and": [
                        {"repo_id": repo_id},
                        {"path": {"$in": list(path_batch)}},
                    ]
                }
                while True:
                    try:
                        page = collection.get(
                            where=where_filter,
                            limit=batch_size,
                            offset=0,
                            include=[],
                        )
                    except Exception as exc:
                        if settings.chroma_mode == "remote":
                            raise RuntimeError(
                                build_remote_chroma_error_message(
                                    settings,
                                    operation=(
                                        "listar documentos para "
                                        "delete_by_repo_and_paths"
                                    ),
                                    exc=exc,
                                    collection_name=collection_name,
                                    batch_size=batch_size,
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
                                    operation="delete_by_repo_and_paths",
                                    exc=exc,
                                    collection_name=collection_name,
                                    batch_size=batch_size,
                                )
                            ) from exc
                        raise
                    deleted_total += len(ids)

            deleted_by_collection[collection_name] = deleted_total

        deleted_by_collection["total"] = sum(deleted_by_collection.values())
        return deleted_by_collection
