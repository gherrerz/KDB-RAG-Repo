"""Validación de salud de almacenamiento para rutas de ingesta y consulta."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from neo4j import GraphDatabase
from openai import OpenAI
from redis import Redis

from coderag.core.lexical_index import (
    repository_has_query_ready_lexical_data,
    repository_lexical_backend_label,
)
from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.core.vector_index import (
    build_managed_vector_index,
    count_repository_vector_collection_documents,
    managed_vector_collection_spaces,
)
from coderag.ingestion.embedding import MODEL_DIMENSIONS
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import (
    ChromaIndex,
    build_remote_chroma_error_message,
)
from coderag.storage.metadata_store import MetadataStore


class StoragePreflightError(RuntimeError):
    """Error lanzado cuando un preflight estricto detecta fallos críticos."""

    def __init__(self, report: dict[str, Any]) -> None:
        """Inicializa el error con el reporte consolidado de salud."""
        self.report = report
        failed_items = [
            item
            for item in report.get("items", [])
            if item.get("critical") and not item.get("ok")
        ]
        if failed_items:
            details = "; ".join(
                f"{item.get('name')}: {item.get('message')}"
                for item in failed_items
            )
            super().__init__(f"Preflight de storage falló: {details}")
            return
        failed = ", ".join(report.get("failed_components", []))
        super().__init__(f"Preflight de storage falló: {failed}")


_CACHE: dict[tuple[str, str | None], dict[str, Any]] = {}
QUERY_COLLECTIONS = ["code_symbols", "code_files", "code_modules"]


def _resolve_embedding_request(
    provider: str | None,
    model: str | None,
) -> tuple[str, str]:
    """Resuelve provider/model efectivos de embeddings para una operación de query."""
    settings = get_settings()
    resolved_provider = settings.resolve_embedding_provider(provider)
    resolved_model = settings.resolve_embedding_model(resolved_provider, model)
    return resolved_provider, resolved_model


def evaluate_embedding_compatibility(
    *,
    runtime_payload: dict[str, str | None] | None,
    requested_embedding_provider: str | None,
    requested_embedding_model: str | None,
) -> dict[str, Any]:
    """Evalúa compatibilidad entre embeddings de última ingesta y consulta actual."""
    requested_provider, requested_model = _resolve_embedding_request(
        requested_embedding_provider,
        requested_embedding_model,
    )

    if not runtime_payload:
        return {
            "embedding_compatible": None,
            "compatibility_reason": "repo_runtime_embedding_unknown",
            "query_embedding_provider": requested_provider,
            "query_embedding_model": requested_model,
            "query_embedding_dimension": MODEL_DIMENSIONS.get(requested_model),
            "last_embedding_dimension": None,
        }

    last_provider_raw = runtime_payload.get("last_embedding_provider")
    last_model_raw = runtime_payload.get("last_embedding_model")
    if not last_provider_raw or not last_model_raw:
        return {
            "embedding_compatible": None,
            "compatibility_reason": "repo_runtime_embedding_unknown",
            "query_embedding_provider": requested_provider,
            "query_embedding_model": requested_model,
            "query_embedding_dimension": MODEL_DIMENSIONS.get(requested_model),
            "last_embedding_dimension": None,
        }

    settings = get_settings()
    last_provider = settings.resolve_embedding_provider(last_provider_raw)
    last_model = settings.resolve_embedding_model(last_provider, last_model_raw)
    query_dimension = MODEL_DIMENSIONS.get(requested_model)
    last_dimension = MODEL_DIMENSIONS.get(last_model)

    compatibility_reason = "embedding_compatible"
    embedding_compatible: bool | None = True

    if query_dimension is None or last_dimension is None:
        embedding_compatible = None
        compatibility_reason = "embedding_dimension_unknown"
    elif query_dimension != last_dimension:
        embedding_compatible = False
        compatibility_reason = "embedding_dimension_mismatch"

    return {
        "embedding_compatible": embedding_compatible,
        "compatibility_reason": compatibility_reason,
        "query_embedding_provider": requested_provider,
        "query_embedding_model": requested_model,
        "query_embedding_dimension": query_dimension,
        "last_embedding_dimension": last_dimension,
    }


def _now_utc_iso() -> str:
    """Devuelve timestamp UTC en formato ISO 8601."""
    return datetime.now(tz=timezone.utc).isoformat()


def _ms_since(started_at: float) -> float:
    """Devuelve milisegundos transcurridos para métricas de latencia."""
    return round((monotonic() - started_at) * 1000.0, 3)


def _error_code(component: str, message: str) -> str:
    """Normaliza códigos de error para diagnóstico operativo."""
    lowered = message.lower()
    if component == "neo4j":
        if "unauthorized" in lowered or "authentication" in lowered:
            return "neo4j_auth_failed"
        if "connection refused" in lowered or "couldn't connect" in lowered:
            return "neo4j_unreachable"
    if component == "chroma":
        if "hnsw" in lowered and "space" in lowered:
            return "chroma_hnsw_space_mismatch"
        if (
            "unauthorized" in lowered
            or "forbidden" in lowered
            or "authentication" in lowered
            or "401" in lowered
            or "403" in lowered
        ):
            return "chroma_auth_failed"
        if "timeout" in lowered or "timed out" in lowered:
            return "chroma_timeout"
        if (
            "name or service not known" in lowered
            or "getaddrinfo" in lowered
            or "temporary failure in name resolution" in lowered
            or "failed to resolve" in lowered
            or "nodename nor servname provided" in lowered
        ):
            return "chroma_dns_failed"
        if (
            "ssl" in lowered
            or "tls" in lowered
            or "certificate" in lowered
            or "cert_verify_failed" in lowered
        ):
            return "chroma_tls_failed"
        if (
            "connection refused" in lowered
            or "couldn't connect" in lowered
            or "failed to establish a new connection" in lowered
            or "connection error" in lowered
            or "network is unreachable" in lowered
            or "no route to host" in lowered
        ):
            return "chroma_unreachable"
        return "chroma_unavailable"
    if component == "metadata_sqlite":
        return "metadata_unavailable"
    if component == "workspace":
        return "workspace_not_writable"
    if component == "openai":
        if "api key" in lowered or "not configured" in lowered:
            return "openai_not_configured"
        return "openai_unavailable"
    if component == "redis":
        return "redis_unavailable"
    if component == "bm25":
        return "bm25_repo_missing"
    return f"{component}_failed"


def _run_component_check(
    *,
    name: str,
    critical: bool,
    check_fn: Any,
) -> dict[str, Any]:
    """Ejecuta una validación individual y retorna resultado estructurado."""
    started_at = monotonic()
    try:
        details = check_fn()
        return {
            "name": name,
            "ok": True,
            "critical": critical,
            "code": "ok",
            "message": "OK",
            "latency_ms": _ms_since(started_at),
            "details": details if isinstance(details, dict) else {},
        }
    except Exception as exc:  # pragma: no cover - depende de infraestructura
        message = str(exc)
        return {
            "name": name,
            "ok": False,
            "critical": critical,
            "code": _error_code(name, message),
            "message": message,
            "latency_ms": _ms_since(started_at),
            "details": {},
        }


def _check_workspace(path: Path) -> dict[str, Any]:
    """Verifica que el workspace exista y tenga permisos de escritura."""
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".storage-health.tmp"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)
    return {"path": str(path)}


def _check_metadata_sqlite(db_path: Path) -> dict[str, Any]:
    """Valida que SQLite de metadatos pueda inicializarse y leerse."""
    store = MetadataStore(db_path)
    repo_ids = store.list_repo_ids()
    return {"db_path": str(db_path), "repo_count": len(repo_ids)}


def _check_metadata_postgres(postgres_dsn: str) -> dict[str, Any]:
    """Valida conectividad básica a Postgres y acceso a tablas de metadatos."""
    from coderag.storage.postgres_metadata_store import PostgresMetadataStore
    store = PostgresMetadataStore(postgres_dsn)
    repo_ids = store.list_repo_ids()
    return {"backend": "postgres", "repo_count": len(repo_ids)}


def _build_metadata_check(settings: object) -> tuple[str, Any]:
    """Resuelve nombre y callable del chequeo de metadata activo."""
    postgres_dsn = resolve_postgres_dsn(settings)
    if postgres_dsn:
        return "metadata_postgres", lambda: _check_metadata_postgres(postgres_dsn)

    metadata_path = settings.workspace_path.parent / "metadata.db"
    return "metadata_sqlite", lambda: _check_metadata_sqlite(metadata_path)


def _check_chroma() -> dict[str, Any]:
    """Valida inicialización y acceso básico a colecciones de Chroma."""
    settings = get_settings()
    index = build_managed_vector_index()
    if settings.chroma_mode == "remote":
        # Verificar conectividad HTTP explícitamente en modo remoto
        try:
            index.client.heartbeat()
        except Exception as exc:
            raise RuntimeError(
                build_remote_chroma_error_message(
                    settings,
                    operation="heartbeat",
                    exc=exc,
                )
            ) from exc
    try:
        collections = index.client.list_collections()
    except Exception as exc:
        if settings.chroma_mode == "remote":
            raise RuntimeError(
                build_remote_chroma_error_message(
                    settings,
                    operation="listar colecciones",
                    exc=exc,
                )
            ) from exc
        raise
    expected_space = settings.resolve_chroma_hnsw_space()
    spaces_by_collection = managed_vector_collection_spaces(index)
    mismatched_collections = sorted(
        name
        for name, detected_space in spaces_by_collection.items()
        if detected_space != expected_space
    )
    if mismatched_collections:
        joined = ", ".join(mismatched_collections)
        raise RuntimeError(
            "Espacio HNSW inconsistente en Chroma. "
            f"Configurado={expected_space}, colecciones={joined}. "
            "Ejecuta reset y reingesta para alinear índices."
        )
    return {
        "mode": settings.chroma_mode,
        "collection_count": len(collections),
        "managed_collection_count": len(index.collections),
        "hnsw_space_configured": expected_space,
        "hnsw_space_detected": spaces_by_collection,
        "hnsw_space_mismatched_collections": mismatched_collections,
    }


def _check_neo4j(timeout_seconds: float) -> dict[str, Any]:
    """Valida conexión Neo4j, autenticación y query mínima de salud."""
    settings = get_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=max(1.0, timeout_seconds),
    )
    try:
        with driver.session() as session:
            record = session.run("RETURN 1 AS ok").single()
        if record is None or int(record["ok"]) != 1:
            raise RuntimeError("Neo4j no respondió correctamente al health query.")
        return {"uri": settings.neo4j_uri}
    finally:
        driver.close()


def _check_bm25(context: str, repo_id: str | None) -> dict[str, Any]:
    """Valida estado BM25 global o por repositorio según contexto."""
    if context in {"query", "inventory_query"}:
        if not repo_id:
            raise RuntimeError(
                "repo_id es requerido para validar BM25 en consulta."
            )
        loaded = GLOBAL_BM25.ensure_repo_loaded(repo_id)
        if not loaded:
            raise RuntimeError(
                f"No hay índice BM25 cargado para repo '{repo_id}'."
            )
        return {"repo_id": repo_id, "indexed": True}
    return {"indexed_repos": GLOBAL_BM25.repo_count()}


def _check_lexical_store(context: str, repo_id: str | None, postgres_dsn: str) -> dict[str, Any]:
    """Valida estado del LexicalStore en Postgres cuando el backend está activo."""
    from coderag.storage.lexical_store import LexicalStore
    settings = get_settings()
    store = LexicalStore(postgres_dsn, settings.lexical_fts_language)
    if context in {"query", "inventory_query"}:
        if not repo_id:
            raise RuntimeError(
                "repo_id es requerido para validar LexicalStore en consulta."
            )
        has = store.has_corpus(repo_id)
        if not has:
            raise RuntimeError(
                f"No hay corpus léxico en Postgres para repo '{repo_id}'."
            )
        return {"repo_id": repo_id, "indexed": True, "backend": "postgres"}
    return {"backend": "postgres"}


def _build_lexical_check(
    settings: object,
    *,
    context: str,
    repo_id: str | None,
) -> tuple[str, Any]:
    """Construye el check léxico apropiado según el backend activo."""
    postgres_dsn = resolve_postgres_dsn(settings)
    label = repository_lexical_backend_label(settings)
    if label == "lexical":
        return label, lambda: _check_lexical_store(
            context=context,
            repo_id=repo_id,
            postgres_dsn=postgres_dsn,
        )
    return label, lambda: _check_bm25(context=context, repo_id=repo_id)


def _check_openai(timeout_seconds: float) -> dict[str, Any]:
    """Valida credenciales OpenAI y conectividad básica con la API."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada.")
    client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=max(1.0, timeout_seconds),
    )
    page = client.models.list(limit=1)
    model_id = page.data[0].id if getattr(page, "data", None) else "unknown"
    return {"model_probe": model_id}


def _check_redis(timeout_seconds: float) -> dict[str, Any]:
    """Valida conectividad Redis para despliegues que lo requieran."""
    settings = get_settings()
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=max(1.0, timeout_seconds),
        socket_timeout=max(1.0, timeout_seconds),
    )
    if not client.ping():
        raise RuntimeError("Redis no respondió PING con éxito.")
    return {"url": settings.redis_url}


def run_storage_preflight(
    *,
    context: str,
    repo_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Ejecuta validaciones de storage y retorna reporte consolidado."""
    settings = get_settings()
    strict = bool(settings.health_check_strict)
    timeout_seconds = max(1.0, float(settings.health_check_timeout_seconds))
    ttl_seconds = max(0.0, float(settings.health_check_ttl_seconds))
    cache_key = (context, repo_id)

    if not force:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            age_ms = (monotonic() - float(cached["cached_at_monotonic"])) * 1000.0
            if age_ms <= ttl_seconds * 1000.0:
                report = dict(cached["report"])
                report["cached"] = True
                return report

    workspace_path = settings.workspace_path
    metadata_check_name, metadata_check_fn = _build_metadata_check(settings)
    lexical_check_name, lexical_check_fn = _build_lexical_check(
        settings,
        context=context,
        repo_id=repo_id,
    )

    workspace_critical = context not in {
        "query",
        "retrieval_query",
        "inventory_query",
    }

    checks_plan: list[dict[str, Any]] = [
        {
            "type": "check",
            "name": "workspace",
            "critical": workspace_critical,
            "check_fn": lambda: _check_workspace(workspace_path),
        },
        {
            "type": "check",
            "name": metadata_check_name,
            "critical": True,
            "check_fn": metadata_check_fn,
        },
        {
            "type": "check",
            "name": "chroma",
            "critical": True,
            "check_fn": _check_chroma,
        },
        {
            "type": "check",
            "name": "neo4j",
            "critical": context != "startup",
            "check_fn": lambda: _check_neo4j(timeout_seconds),
        },
        {
            "type": "check",
            "name": lexical_check_name,
            # No crítico, solo advertencia si falta
            "critical": False,
            "check_fn": lexical_check_fn,
        },
    ]

    if settings.health_check_openai:
        checks_plan.append(
            {
                "type": "check",
                "name": "openai",
                "critical": True,
                "check_fn": lambda: _check_openai(timeout_seconds),
            }
        )
    else:
        checks_plan.append(
            {
                "type": "static",
                "item": {
                    "name": "openai",
                    "ok": True,
                    "critical": False,
                    "code": "skipped",
                    "message": "Chequeo OpenAI deshabilitado por configuración.",
                    "latency_ms": 0.0,
                    "details": {},
                },
            }
        )

    if settings.health_check_redis:
        checks_plan.append(
            {
                "type": "check",
                "name": "redis",
                "critical": False,
                "check_fn": lambda: _check_redis(timeout_seconds),
            }
        )
    else:
        checks_plan.append(
            {
                "type": "static",
                "item": {
                    "name": "redis",
                    "ok": True,
                    "critical": False,
                    "code": "skipped",
                    "message": "Chequeo Redis deshabilitado por configuración.",
                    "latency_ms": 0.0,
                    "details": {},
                },
            }
        )

    check_entries = [entry for entry in checks_plan if entry["type"] == "check"]
    max_workers = min(8, max(1, len(check_entries)))
    results_by_name: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_by_name = {
            str(entry["name"]): executor.submit(
                _run_component_check,
                name=str(entry["name"]),
                critical=bool(entry["critical"]),
                check_fn=entry["check_fn"],
            )
            for entry in check_entries
        }
        results_by_name = {
            name: future.result()
            for name, future in future_by_name.items()
        }

    items: list[dict[str, Any]] = []
    for entry in checks_plan:
        if entry["type"] == "check":
            items.append(results_by_name[str(entry["name"])])
            continue
        items.append(entry["item"])

    failed_components = [
        item["name"] for item in items if (item["critical"] and not item["ok"])
    ]

    report = {
        "ok": len(failed_components) == 0,
        "strict": strict,
        "checked_at": _now_utc_iso(),
        "context": context,
        "repo_id": repo_id,
        "failed_components": failed_components,
        "items": items,
        "cached": False,
    }

    _CACHE[cache_key] = {
        "cached_at_monotonic": monotonic(),
        "report": report,
    }
    return report


def ensure_storage_ready(
    *,
    context: str,
    repo_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Ejecuta preflight y lanza error cuando la política estricta detecta fallos."""
    report = run_storage_preflight(context=context, repo_id=repo_id, force=force)
    if report["strict"] and not report["ok"]:
        raise StoragePreflightError(report)
    return report


def _count_chroma_documents_for_repo(
    repo_id: str,
    collection_name: str,
    page_size: int = 500,
) -> int:
    """Cuenta documentos de un repositorio en una colección Chroma paginando por offset."""
    index = build_managed_vector_index()
    if collection_name not in index.collections:
        return 0
    return count_repository_vector_collection_documents(
        index,
        repo_id=repo_id,
        collection_name=collection_name,
        page_size=page_size,
    )


def _count_query_collections_for_repo(repo_id: str) -> dict[str, int | None]:
    """Cuenta documentos Chroma por colección para un repositorio."""
    return {
        collection_name: _count_chroma_documents_for_repo(
            repo_id=repo_id,
            collection_name=collection_name,
        )
        for collection_name in QUERY_COLLECTIONS
    }


def _check_repo_graph_available(repo_id: str, timeout_seconds: float) -> bool:
    """Determina si existen nodos asociados al repo en Neo4j."""
    settings = get_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=max(1.0, timeout_seconds),
    )
    try:
        with driver.session() as session:
            record = session.run(
                "MATCH (n {repo_id: $repo_id}) RETURN count(n) AS total",
                repo_id=repo_id,
            ).single()
        if record is None:
            return False
        return int(record["total"]) > 0
    finally:
        driver.close()


def get_repo_query_status(
    *,
    repo_id: str,
    listed_in_catalog: bool,
    runtime_payload: dict[str, str | None] | None = None,
    requested_embedding_provider: str | None = None,
    requested_embedding_model: str | None = None,
) -> dict[str, Any]:
    """Evalúa si un repositorio está listo para consultas RAG."""
    settings = get_settings()
    workspace_available = (settings.workspace_path / repo_id).is_dir()
    warnings: list[str] = []
    chroma_counts: dict[str, int | None] = {}
    configured_hnsw_space = settings.resolve_chroma_hnsw_space()
    chroma_spaces: dict[str, str | None] = {}
    chroma_space_mismatched_collections: list[str] = []

    lexical_backend = repository_lexical_backend_label(settings)
    lexical_loaded = repository_has_query_ready_lexical_data(settings, repo_id)
    if not lexical_loaded:
        if lexical_backend == "lexical":
            warnings.append(f"No hay corpus léxico en Postgres para repo '{repo_id}'.")
        else:
            warnings.append(f"No hay indice BM25 en memoria para repo '{repo_id}'.")

    try:
        chroma_counts = _count_query_collections_for_repo(repo_id)
        if not any((count or 0) > 0 for count in chroma_counts.values()):
            if listed_in_catalog or lexical_loaded:
                ChromaIndex.reset_shared_state()
                chroma_counts = _count_query_collections_for_repo(repo_id)
    except Exception as exc:  # pragma: no cover - depende de infraestructura
        chroma_counts = {collection_name: None for collection_name in QUERY_COLLECTIONS}
        warnings.append(f"No se pudo contar documentos del repo en Chroma: {exc}")

    try:
        chroma_spaces = managed_vector_collection_spaces(
            build_managed_vector_index()
        )
        chroma_space_mismatched_collections = sorted(
            name
            for name, detected_space in chroma_spaces.items()
            if detected_space != configured_hnsw_space
        )
        if chroma_space_mismatched_collections:
            warnings.append(
                "Espacio HNSW inconsistente en Chroma. "
                f"Configurado={configured_hnsw_space}, "
                "colecciones desalineadas="
                f"{', '.join(chroma_space_mismatched_collections)}. "
                "Ejecuta reset y reingesta para alinear índices."
            )
    except Exception as exc:
        warnings.append(f"No se pudo validar hnsw.space en Chroma: {exc}")

    graph_available: bool | None = None
    try:
        graph_available = _check_repo_graph_available(
            repo_id=repo_id,
            timeout_seconds=max(1.0, float(settings.health_check_timeout_seconds)),
        )
    except Exception as exc:  # pragma: no cover - depende de infraestructura
        warnings.append(f"Neo4j no disponible para validar repo '{repo_id}': {exc}")

    embedding_compatibility = evaluate_embedding_compatibility(
        runtime_payload=runtime_payload,
        requested_embedding_provider=requested_embedding_provider,
        requested_embedding_model=requested_embedding_model,
    )
    embedding_compatible = embedding_compatibility.get("embedding_compatible")
    if embedding_compatible is False:
        warnings.append(
            "El modelo/provider de embeddings de consulta no es compatible con "
            "la última ingesta del repositorio."
        )

    chroma_has_docs = any((count or 0) > 0 for count in chroma_counts.values())
    chroma_space_compatible = len(chroma_space_mismatched_collections) == 0
    query_ready = bool(
        chroma_has_docs
        and lexical_loaded
        and embedding_compatible is not False
        and chroma_space_compatible
    )
    return {
        "repo_id": repo_id,
        "listed_in_catalog": listed_in_catalog,
        "workspace_available": workspace_available,
        "query_ready": query_ready,
        "chroma_counts": chroma_counts,
        "chroma_hnsw_space_configured": configured_hnsw_space,
        "chroma_hnsw_space_detected": chroma_spaces,
        "chroma_hnsw_space_compatible": chroma_space_compatible,
        "chroma_hnsw_space_mismatched_collections": chroma_space_mismatched_collections,
        "bm25_loaded": lexical_loaded,
        "lexical_loaded": lexical_loaded,
        "graph_available": graph_available,
        "embedding_compatible": embedding_compatible,
        "compatibility_reason": embedding_compatibility["compatibility_reason"],
        "warnings": warnings,
    }

