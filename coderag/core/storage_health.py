"""Validación de salud de almacenamiento para rutas de ingesta y consulta."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from neo4j import GraphDatabase
from openai import OpenAI
from redis import Redis

from coderag.core.settings import get_settings
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import ChromaIndex
from coderag.storage.metadata_store import MetadataStore


class StoragePreflightError(RuntimeError):
    """Error lanzado cuando un preflight estricto detecta fallos críticos."""

    def __init__(self, report: dict[str, Any]) -> None:
        """Inicializa el error con el reporte consolidado de salud."""
        self.report = report
        failed = ", ".join(report.get("failed_components", []))
        super().__init__(f"Preflight de storage falló: {failed}")


_CACHE: dict[tuple[str, str | None], dict[str, Any]] = {}


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


def _check_chroma() -> dict[str, Any]:
    """Valida inicialización y acceso básico a colecciones de Chroma."""
    index = ChromaIndex()
    collections = index.client.list_collections()
    return {
        "collection_count": len(collections),
        "managed_collection_count": len(index.collections),
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
            return {"repo_id": None, "indexed": False, "ok": False, "critical": False, "message": "repo_id es requerido para validar BM25 en consulta."}
        if not GLOBAL_BM25.has_repo(repo_id):
            return {"repo_id": repo_id, "indexed": False, "ok": False, "critical": False, "message": f"No hay índice BM25 cargado para repo '{repo_id}'."}
        return {"repo_id": repo_id, "indexed": True, "ok": True, "critical": False}
    return {"indexed_repos": GLOBAL_BM25.repo_count(), "ok": True, "critical": False}


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
    metadata_path = settings.workspace_path.parent / "metadata.db"

    items = [
        _run_component_check(
            name="workspace",
            critical=True,
            check_fn=lambda: _check_workspace(workspace_path),
        ),
        _run_component_check(
            name="metadata_sqlite",
            critical=True,
            check_fn=lambda: _check_metadata_sqlite(metadata_path),
        ),
        _run_component_check(name="chroma", critical=True, check_fn=_check_chroma),
        _run_component_check(
            name="neo4j",
            critical=True,
            check_fn=lambda: _check_neo4j(timeout_seconds),
        ),
        _run_component_check(
            name="bm25",
            critical=False,  # BM25 is not critical, just warn if missing
            check_fn=lambda: _check_bm25(context=context, repo_id=repo_id),
        ),
    ]

    if settings.health_check_openai:
        items.append(
            _run_component_check(
                name="openai",
                critical=True,
                check_fn=lambda: _check_openai(timeout_seconds),
            )
        )
    else:
        items.append(
            {
                "name": "openai",
                "ok": True,
                "critical": False,
                "code": "skipped",
                "message": "Chequeo OpenAI deshabilitado por configuración.",
                "latency_ms": 0.0,
                "details": {},
            }
        )

    if settings.health_check_redis:
        items.append(
            _run_component_check(
                name="redis",
                critical=False,
                check_fn=lambda: _check_redis(timeout_seconds),
            )
        )
    else:
        items.append(
            {
                "name": "redis",
                "ok": True,
                "critical": False,
                "code": "skipped",
                "message": "Chequeo Redis deshabilitado por configuración.",
                "latency_ms": 0.0,
                "details": {},
            }
        )

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

