"""Servidor FastAPI para operaciones de ingesta y consulta."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from time import perf_counter
from typing import cast

from fastapi import FastAPI, Header, HTTPException, Query

from coderag.core.logging import configure_logging
from coderag.core.models import (
    ChromaDiagnosticsCollectionResult,
    ChromaDiagnosticsResponse,
    ChromaQueryOperation,
    ChromaQueryRequest,
    ChromaQueryResponse,
    InventoryQueryRequest,
    InventoryQueryResponse,
    JobInfo,
    ProviderModelCatalogResponse,
    QueryRequest,
    QueryResponse,
    RepoCatalogEntry,
    RepoLastQueryStaleResponse,
    RepoRuntimeEntry,
    RetrievalQueryRequest,
    RetrievalQueryResponse,
    RepoCatalogResponse,
    RepoDeleteResponse,
    RepoQueryStatusResponse,
    RepoIngestRequest,
    ResetResponse,
    StorageHealthResponse,
)
from coderag.core.storage_health import (
    StoragePreflightError,
    ensure_storage_ready,
    get_repo_query_status,
    run_storage_preflight,
)
from coderag.core.settings import get_settings
from coderag.storage.postgres_startup import ensure_postgres_schema_ready
from coderag.core.vector_index import build_managed_vector_index
from coderag.jobs.worker import IngestionConflictError, JobManager
from coderag.llm.model_discovery import discover_models


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ejecuta validación estricta de storage durante el arranque de la API."""
    settings = get_settings()
    if hasattr(settings, "decode_vertex_service_account_b64"):
        settings.decode_vertex_service_account_b64()
    app.state.postgres_startup = ensure_postgres_schema_ready(settings)
    app.state.job_manager = jobs
    report = ensure_storage_ready(context="startup", force=True)
    app.state.storage_health = _attach_postgres_startup_status(report)
    yield


configure_logging()
app = FastAPI(
    title="RAG Hybrid Response Validator API",
    version="0.1.0",
    description=(
        "API para ingesta y consulta sobre repositorios de código usando "
        "retrieval híbrido (vector + capa léxica + grafo).\n\n"
        "Incluye endpoints de operacion (ingesta, query, inventario), "
        "readiness por repositorio y salud de storage."
    ),
    summary=(
        "Servicios HTTP para ingesta asíncrona, consultas con evidencia y "
        "observabilidad operativa."
    ),
    contact={
        "name": "Coderag API",
        "url": "http://127.0.0.1:8000/docs",
    },
    lifespan=lifespan,
)
jobs = JobManager()


def get_job_manager() -> JobManager:
    """Resuelve el gestor de jobs activo con soporte para overrides en app.state."""
    override = getattr(app.state, "job_manager_override", None)
    if override is not None:
        return cast(JobManager, override)
    return cast(JobManager, jobs)


def _normalize_repo_query_status_payload(
    status_payload: dict[str, object],
) -> dict[str, object]:
    """Normaliza readiness léxico al contrato público actual."""
    return dict(status_payload)


def _attach_postgres_startup_status(
    report: dict[str, object],
) -> dict[str, object]:
    """Adjunta el estado de bootstrap Postgres al payload si existe."""
    normalized = dict(report)
    postgres_startup = getattr(app.state, "postgres_startup", None)
    if postgres_startup is not None:
        normalized["postgres_startup"] = postgres_startup
    return normalized


def _build_repo_query_readiness(
    *,
    job_manager: JobManager,
    repo_id: str,
    requested_embedding_provider: str | None,
    requested_embedding_model: str | None,
) -> dict[str, object]:
    """Construye el payload normalizado de readiness para un repositorio."""
    listed_repo_ids = job_manager.list_repo_ids()
    runtime_payload = job_manager.get_repo_runtime(repo_id)
    readiness = get_repo_query_status(
        repo_id=repo_id,
        listed_in_catalog=repo_id in listed_repo_ids,
        runtime_payload=runtime_payload,
        requested_embedding_provider=requested_embedding_provider,
        requested_embedding_model=requested_embedding_model,
    )
    if runtime_payload:
        readiness.update(runtime_payload)
    return _normalize_repo_query_status_payload(readiness)


def _mark_repo_as_queried(*, job_manager: JobManager, repo_id: str) -> None:
    """Marca el repo como consultado cuando entra a un flujo válido."""
    job_manager.touch_repo_last_queried_at(repo_id)


def _ensure_repo_query_ready(readiness: dict[str, object]) -> None:
    """Valida compatibilidad y readiness para endpoints de consulta."""
    if readiness.get("embedding_compatible") is False:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "El embedding seleccionado para consulta no es compatible "
                    "con la última ingesta del repositorio. Reingesta con el "
                    "mismo modelo/provider o limpia índices antes de consultar."
                ),
                "code": "embedding_incompatible",
                "repo_status": readiness,
            },
        )

    if not bool(readiness["query_ready"]):
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "El repositorio no está listo para consultas. "
                    "Reingesta el repositorio o revisa el estado de índices."
                ),
                "code": "repo_not_ready",
                "repo_status": readiness,
            },
        )


def _ensure_chroma_admin_access(admin_token: str | None) -> None:
    """Protege endpoints administrativos de Chroma con flag y token opcional."""
    settings = get_settings()
    if not bool(getattr(settings, "chroma_admin_api_enabled", False)):
        raise HTTPException(
            status_code=404,
            detail={
                "message": "El endpoint administrativo de Chroma está deshabilitado.",
                "code": "chroma_admin_disabled",
            },
        )

    expected_token = str(
        getattr(settings, "chroma_admin_api_token", "") or ""
    ).strip()
    if expected_token and (admin_token or "").strip() != expected_token:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Token administrativo inválido para endpoint Chroma.",
                "code": "invalid_chroma_admin_token",
            },
        )


def _resolve_chroma_collection_names(
    requested_names: list[str] | None,
) -> tuple[object, list[str]]:
    """Valida y retorna las colecciones gestionadas a consultar."""
    index = build_managed_vector_index()
    available_names = index.list_collection_names()
    if not requested_names:
        return index, available_names

    unique_names = list(dict.fromkeys(requested_names))
    invalid_names = sorted(
        name for name in unique_names if name not in available_names
    )
    if invalid_names:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Se solicitaron colecciones Chroma no gestionadas.",
                "code": "invalid_chroma_collection",
                "invalid_collections": invalid_names,
                "available_collections": available_names,
            },
        )
    return index, unique_names


def _validate_chroma_collection_name(
    index: object,
    collection_name: str | None,
) -> str | None:
    """Valida que una colección exista dentro del backend gestionado."""
    if collection_name is None:
        return None

    available_names = index.list_collection_names()
    if collection_name not in available_names:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "La colección solicitada no existe en Chroma gestionado.",
                "code": "invalid_chroma_collection",
                "collection_name": collection_name,
                "available_collections": available_names,
            },
        )
    return collection_name


def _build_chroma_effective_params(
    request: ChromaQueryRequest,
) -> dict[str, object]:
    """Serializa solo los parámetros efectivos del request a Chroma."""
    params: dict[str, object] = {}
    if request.collection_name is not None:
        params["collection_name"] = request.collection_name
    if request.where is not None:
        params["where"] = request.where
    if request.where_document is not None:
        params["where_document"] = request.where_document
    if request.include is not None:
        params["include"] = request.include
    if request.limit is not None:
        params["limit"] = request.limit
    if request.offset is not None:
        params["offset"] = request.offset
    if request.n_results is not None:
        params["n_results"] = request.n_results
    if request.query_texts is not None:
        params["query_texts"] = request.query_texts
    return params


@app.post(
    "/repos/ingest",
    response_model=JobInfo,
    tags=["Ingesta"],
    summary="Crear job de ingesta",
    description=(
        "Inicia una ingesta asíncrona del repositorio y retorna el estado "
        "inicial del job."
    ),
    responses={
        503: {
            "description": "Preflight de storage falló antes de iniciar ingesta.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "message": "Preflight de storage falló antes de ingesta.",
                            "health": {
                                "ok": False,
                                "failed_components": ["chroma"],
                            },
                        }
                    }
                }
            },
        }
    },
)
def ingest_repo(request: RepoIngestRequest) -> JobInfo:
    """Cree un trabajo de ingesta y devuelva el estado inicial del trabajo."""
    job_manager = get_job_manager()
    try:
        ensure_storage_ready(context="ingest", repo_id=None)
    except StoragePreflightError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Preflight de storage falló antes de ingesta.",
                "health": exc.report,
            },
        ) from exc
    try:
        return job_manager.create_ingest_job(request)
    except IngestionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ya existe una ingesta activa para el repositorio.",
                "error": str(exc),
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "No se pudo iniciar la ingesta asíncrona.",
                "error": str(exc),
            },
        ) from exc


@app.get(
    "/jobs/{job_id}",
    response_model=JobInfo,
    tags=["Ingesta"],
    summary="Consultar estado de job",
    description=(
        "Obtiene estado, progreso y logs del job de ingesta. "
        "Permite acotar la cola de logs para reducir latencia de polling."
    ),
    responses={
        404: {
            "description": "No existe un job con ese identificador.",
            "content": {
                "application/json": {
                    "example": {"detail": "Job no encontrado"}
                }
            },
        }
    },
)
def get_job(
    job_id: str,
    logs_tail: int = Query(
        default=200,
        ge=0,
        le=2000,
        description="Cantidad máxima de líneas de log a devolver desde el final.",
    ),
) -> JobInfo:
    """Devuelve el estado actual del trabajo de ingesta con cola de logs acotada."""
    job = get_job_manager().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    if logs_tail == 0:
        selected_logs: list[str] = []
    else:
        selected_logs = list(job.logs[-logs_tail:])

    return JobInfo(
        id=job.id,
        status=job.status,
        progress=job.progress,
        logs=selected_logs,
        repo_id=job.repo_id,
        error=job.error,
        diagnostics=job.diagnostics,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@app.post(
    "/query",
    response_model=QueryResponse,
    tags=["Consulta"],
    summary="Consulta híbrida general",
    description=(
        "Ejecuta retrieval híbrido para responder preguntas con citas. "
        "Valida readiness del repo antes de consultar."
    ),
    responses={
        422: {
            "description": "Repositorio no listo para consultas.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "message": (
                                "El repositorio no está listo para consultas. "
                                "Reingesta el repositorio o revisa el estado de índices."
                            ),
                            "code": "repo_not_ready",
                            "repo_status": {
                                "repo_id": "mall",
                                "listed_in_catalog": True,
                                "query_ready": False,
                                "chroma_counts": {
                                    "code_symbols": 0,
                                    "code_files": 0,
                                    "code_modules": 0,
                                },
                                "lexical_loaded": False,
                                "graph_available": None,
                                "warnings": [
                                    "No hay corpus léxico listo para repo 'mall'."
                                ],
                            },
                        }
                    }
                }
            },
        },
        503: {
            "description": "Preflight de storage falló antes de consulta.",
        },
    },
)
def query_repo(request: QueryRequest) -> QueryResponse:
    """Ejecute una canalización de consultas híbrida para un repositorio indexado."""
    from coderag.api.query_service import run_query

    job_manager = get_job_manager()
    try:
        ensure_storage_ready(context="query", repo_id=request.repo_id)
    except StoragePreflightError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Preflight de storage falló antes de consulta.",
                "health": exc.report,
            },
        ) from exc

    readiness = _build_repo_query_readiness(
        job_manager=job_manager,
        repo_id=request.repo_id,
        requested_embedding_provider=request.embedding_provider,
        requested_embedding_model=request.embedding_model,
    )
    _ensure_repo_query_ready(readiness)
    _mark_repo_as_queried(job_manager=job_manager, repo_id=request.repo_id)

    return run_query(
        repo_id=request.repo_id,
        query=request.query,
        top_n=request.top_n,
        top_k=request.top_k,
        embedding_provider=request.embedding_provider,
        embedding_model=request.embedding_model,
        llm_provider=request.llm_provider,
        answer_model=request.answer_model,
        verifier_model=request.verifier_model,
    )


@app.post(
    "/inventory/query",
    response_model=InventoryQueryResponse,
    tags=["Consulta"],
    summary="Consulta de inventario paginada",
    description=(
        "Consulta orientada a inventarios amplios (ejemplo: todos los "
        "controllers de un modulo), con paginación y diagnostics."
    ),
    responses={
        503: {
            "description": "Preflight de storage falló antes de consulta de inventario.",
        }
    },
)
def query_inventory(request: InventoryQueryRequest) -> InventoryQueryResponse:
    """Ejecute una consulta de inventario paginado primero en el gráfico para obtener intenciones de lista amplia."""
    from coderag.api.query_service import run_inventory_query

    try:
        ensure_storage_ready(context="inventory_query", repo_id=request.repo_id)
    except StoragePreflightError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Preflight de storage falló antes de inventario.",
                "health": exc.report,
            },
        ) from exc

    job_manager = get_job_manager()
    _mark_repo_as_queried(job_manager=job_manager, repo_id=request.repo_id)

    return run_inventory_query(
        repo_id=request.repo_id,
        query=request.query,
        page=request.page,
        page_size=request.page_size,
    )


@app.post(
    "/query/retrieval",
    response_model=RetrievalQueryResponse,
    tags=["Consulta"],
    summary="Consulta retrieval-only sin LLM",
    description=(
        "Ejecuta retrieval híbrido y retorna evidencia estructurada sin síntesis "
        "con LLM. Mantiene las validaciones de readiness del repositorio."
    ),
    responses={
        422: {
            "description": "Repositorio no listo o embedding incompatible para consulta.",
        },
        503: {
            "description": "Preflight de storage falló antes de retrieval.",
        },
    },
)
def query_retrieval(request: RetrievalQueryRequest) -> RetrievalQueryResponse:
    """Ejecuta consulta retrieval-only y devuelve evidencia sin sintetizar con LLM."""
    from coderag.api.query_service import run_retrieval_query

    job_manager = get_job_manager()
    try:
        ensure_storage_ready(context="retrieval_query", repo_id=request.repo_id)
    except StoragePreflightError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Preflight de storage falló antes de retrieval.",
                "health": exc.report,
            },
        ) from exc

    readiness = _build_repo_query_readiness(
        job_manager=job_manager,
        repo_id=request.repo_id,
        requested_embedding_provider=request.embedding_provider,
        requested_embedding_model=request.embedding_model,
    )
    _ensure_repo_query_ready(readiness)
    _mark_repo_as_queried(job_manager=job_manager, repo_id=request.repo_id)

    return run_retrieval_query(
        repo_id=request.repo_id,
        query=request.query,
        top_n=request.top_n,
        top_k=request.top_k,
        embedding_provider=request.embedding_provider,
        embedding_model=request.embedding_model,
        include_context=request.include_context,
    )


@app.get(
    "/repos",
    response_model=RepoCatalogResponse,
    tags=["Catalogo"],
    summary="Listar repositorios disponibles",
    description="Lista los repo_id disponibles para ser usados en consultas.",
)
def list_repos() -> RepoCatalogResponse:
    """Devuelve ids y metadata básica de repositorios disponibles para consultas."""
    job_manager = get_job_manager()
    repositories = [
        RepoCatalogEntry(
            repo_id=str(item["repo_id"]),
            organization=item.get("organization"),
            url=item.get("url"),
            branch=item.get("branch"),
        )
        for item in job_manager.list_repo_catalog()
    ]
    return RepoCatalogResponse(
        repo_ids=[item.repo_id for item in repositories],
        repositories=repositories,
    )


@app.get(
    "/repos/last-query/stale",
    response_model=RepoLastQueryStaleResponse,
    tags=["Catalogo"],
    summary="Listar repositorios sin consultas recientes",
    description=(
        "Retorna repositorios cuya última consulta es menor o igual a una "
        "fecha de corte, incluyendo repos nunca consultados."
    ),
)
def list_stale_repos(
    last_queried_on_or_before: datetime = Query(
        ...,
        description=(
            "Fecha de corte ISO-8601. Incluye repos con last_queried_at <= "
            "este valor y repos con last_queried_at null."
        ),
    ),
) -> RepoLastQueryStaleResponse:
    """Lista repositorios sin consultas recientes para una fecha de corte."""
    job_manager = get_job_manager()
    repositories = [
        RepoRuntimeEntry(
            repo_id=str(item["repo_id"]),
            organization=(
                str(item["organization"])
                if item.get("organization") is not None
                else None
            ),
            url=str(item["url"]) if item.get("url") is not None else None,
            branch=(
                str(item["branch"]) if item.get("branch") is not None else None
            ),
            local_path=(
                str(item["local_path"])
                if item.get("local_path") is not None
                else None
            ),
            created_at=item["created_at"],
            updated_at=item.get("updated_at"),
            last_queried_at=item.get("last_queried_at"),
        )
        for item in job_manager.list_stale_repos(
            last_queried_on_or_before=last_queried_on_or_before,
        )
    ]
    return RepoLastQueryStaleResponse(
        last_queried_on_or_before=last_queried_on_or_before,
        repositories=repositories,
    )


@app.get(
    "/providers/models",
    response_model=ProviderModelCatalogResponse,
    tags=["Catalogo"],
    summary="Catálogo de modelos por provider",
    description=(
        "Obtiene modelos de embeddings o LLM por provider. "
        "Intenta discovery remoto y aplica fallback local cuando corresponde."
    ),
)
def list_provider_models(
    provider: str,
    kind: str,
    force_refresh: bool = False,
) -> ProviderModelCatalogResponse:
    """Lista modelos por provider/tipo para poblar selectores de UI."""
    result = discover_models(
        provider=provider,
        kind=kind,
        force_refresh=force_refresh,
    )
    return ProviderModelCatalogResponse(
        provider=result.provider,
        kind=result.kind,
        models=result.models,
        source=result.source,
        warning=result.warning,
    )


@app.get(
    "/repos/{repo_id}/status",
    response_model=RepoQueryStatusResponse,
    tags=["Catalogo"],
    summary="Estado de readiness por repositorio",
    description=(
        "Evalúa si un repo está listo para /query, incluyendo conteos en "
        "Chroma, capa léxica y disponibilidad de grafo."
    ),
)
def repo_status(
    repo_id: str,
    requested_embedding_provider: str | None = None,
    requested_embedding_model: str | None = None,
) -> RepoQueryStatusResponse:
    """Devuelve estado de disponibilidad de consulta para un repositorio."""
    job_manager = get_job_manager()
    status_payload = _build_repo_query_readiness(
        job_manager=job_manager,
        repo_id=repo_id,
        requested_embedding_provider=requested_embedding_provider,
        requested_embedding_model=requested_embedding_model,
    )
    return RepoQueryStatusResponse(**status_payload)


@app.get(
    "/health",
    response_model=StorageHealthResponse,
    tags=["Admin"],
    summary="Salud de storage",
    description=(
        "Ejecuta preflight de storage y devuelve el estado consolidado de "
        "componentes críticos y no críticos."
    ),
)
def storage_health() -> StorageHealthResponse:
    """Devuelve estado de salud de componentes de almacenamiento del RAG."""
    report = _attach_postgres_startup_status(
        run_storage_preflight(context="health", force=True)
    )
    app.state.storage_health = report
    return StorageHealthResponse(**report)


@app.get(
    "/admin/chroma/diagnostics",
    response_model=ChromaDiagnosticsResponse,
    tags=["Admin"],
    summary="Diagnóstico directo de Chroma",
    description=(
        "Retorna conteos y metadata de colecciones gestionadas de Chroma, "
        "con opción de contar también por repo_id."
    ),
    responses={
        422: {"description": "Se solicitaron colecciones no gestionadas."},
        503: {"description": "No se pudo construir el diagnóstico en Chroma."},
    },
)
def chroma_diagnostics(
    repo_id: str | None = None,
    collection_names: list[str] | None = Query(
        default=None,
        description="Lista opcional de colecciones gestionadas a evaluar.",
    ),
    page_size: int = Query(
        default=500,
        ge=1,
        le=5000,
        description="Tamaño de página usado para conteos paginados.",
    ),
    x_chroma_admin_token: str | None = Header(
        default=None,
        alias="X-Chroma-Admin-Token",
    ),
) -> ChromaDiagnosticsResponse:
    """Expone un resumen operativo de Chroma útil para soporte."""
    _ensure_chroma_admin_access(x_chroma_admin_token)
    settings = get_settings()
    index, selected_names = _resolve_chroma_collection_names(collection_names)
    warnings: list[str] = []
    results: list[ChromaDiagnosticsCollectionResult] = []

    for collection_name in selected_names:
        total_count: int | None = None
        repo_count: int | None = None
        metadata: dict[str, object] = {}
        error: str | None = None
        try:
            total_count = index.count_collection(
                collection_name,
                page_size=page_size,
            )
            metadata = index.get_collection_metadata(collection_name)
            if repo_id is not None:
                repo_count = index.count_by_repo_id(
                    collection_name,
                    repo_id,
                    page_size=page_size,
                )
        except Exception as exc:
            error = str(exc)
            warnings.append(f"{collection_name}: {exc}")

        results.append(
            ChromaDiagnosticsCollectionResult(
                collection_name=collection_name,
                total_count=total_count,
                repo_count=repo_count,
                metadata=metadata,
                error=error,
            )
        )

    if results and all(item.error is not None for item in results):
        raise HTTPException(
            status_code=503,
            detail={
                "message": "No se pudo construir el diagnóstico de Chroma.",
                "code": "chroma_diagnostics_failed",
                "warnings": warnings,
            },
        )

    return ChromaDiagnosticsResponse(
        chroma_mode=str(settings.chroma_mode),
        repo_id=repo_id,
        collection_names=selected_names,
        partial=any(item.error is not None for item in results),
        warnings=warnings,
        collections=results,
    )


@app.post(
    "/admin/chroma/query",
    response_model=ChromaQueryResponse,
    tags=["Admin"],
    summary="Consulta directa controlada a Chroma",
    description=(
        "Ejecuta operaciones de lectura permitidas sobre Chroma para "
        "diagnóstico y soporte operativo."
    ),
    responses={
        422: {"description": "Payload inválido o colección no gestionada."},
        503: {"description": "Fallo al ejecutar la operación en Chroma."},
    },
)
def chroma_query(
    request: ChromaQueryRequest,
    x_chroma_admin_token: str | None = Header(
        default=None,
        alias="X-Chroma-Admin-Token",
    ),
) -> ChromaQueryResponse:
    """Ejecuta una operación de lectura permitida sobre Chroma."""
    started_at = perf_counter()
    _ensure_chroma_admin_access(x_chroma_admin_token)
    index = build_managed_vector_index()
    collection_name = _validate_chroma_collection_name(
        index,
        request.collection_name,
    )

    try:
        if request.operation == ChromaQueryOperation.list_collections:
            result: object = {"collection_names": index.list_collection_names()}
        elif request.operation == ChromaQueryOperation.collection_count:
            assert collection_name is not None
            result = {
                "count": index.count_collection(
                    collection_name,
                    where=request.where,
                ),
            }
        elif request.operation == ChromaQueryOperation.collection_metadata:
            assert collection_name is not None
            result = index.get_collection_metadata(collection_name)
        elif request.operation == ChromaQueryOperation.get:
            assert collection_name is not None
            result = index.get_collection(
                collection_name,
                where=request.where,
                where_document=request.where_document,
                include=request.include,
                limit=request.limit,
                offset=request.offset,
            )
        elif request.operation == ChromaQueryOperation.peek:
            assert collection_name is not None
            result = index.get_collection(
                collection_name,
                include=request.include,
                limit=request.limit,
                offset=0,
            )
        else:
            assert collection_name is not None
            result = index.query_collection(
                collection_name,
                query_texts=request.query_texts,
                n_results=request.n_results or 10,
                where=request.where,
                where_document=request.where_document,
                include=request.include,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "La operación de lectura en Chroma falló.",
                "code": "chroma_query_failed",
                "operation": request.operation.value,
                "collection_name": collection_name,
                "error": str(exc),
            },
        ) from exc

    elapsed_ms = round((perf_counter() - started_at) * 1000, 3)
    return ChromaQueryResponse(
        operation=request.operation,
        collection_name=collection_name,
        effective_params=_build_chroma_effective_params(request),
        result=result,
        warnings=[],
        elapsed_ms=elapsed_ms,
    )


@app.post(
    "/admin/reset",
    response_model=ResetResponse,
    tags=["Admin"],
    summary="Limpieza total de estado",
    description=(
        "Limpia índices, metadata y workspace de ingesta. Rechaza la acción "
        "si hay jobs en ejecución."
    ),
    responses={
        409: {
            "description": "No se puede limpiar mientras hay jobs en ejecución.",
        },
        500: {
            "description": "Error inesperado durante el proceso de limpieza.",
        },
    },
)
def reset_all_data() -> ResetResponse:
    """Restablezca todos los almacenes de datos indexados y el espacio de trabajo de ingesta local."""
    job_manager = get_job_manager()
    try:
        cleared, warnings = job_manager.reset_all_data()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ResetResponse(
        message="Limpieza total completada",
        cleared=cleared,
        warnings=warnings,
    )


@app.delete(
    "/repos/{repo_id}",
    response_model=RepoDeleteResponse,
    tags=["Admin"],
    summary="Eliminar repositorio por ID",
    description=(
        "Elimina un repositorio de Chroma, capa léxica, Neo4j, workspace y "
        "metadata operativa. Rechaza la acción si hay jobs activos del mismo repo."
    ),
    responses={
        404: {
            "description": "No existe un repositorio con ese identificador.",
        },
        409: {
            "description": "Hay ingestas activas del mismo repositorio.",
        },
        500: {
            "description": "Error inesperado durante el proceso de eliminación.",
        },
    },
)
def delete_repo(repo_id: str) -> RepoDeleteResponse:
    """Elimine un repositorio específico de todas las capas de almacenamiento."""
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise HTTPException(status_code=422, detail="repo_id no puede estar vacío")

    job_manager = get_job_manager()
    try:
        cleared, warnings, deleted_counts = job_manager.delete_repo(normalized_repo_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RepoDeleteResponse(
        message=f"Repositorio '{normalized_repo_id}' eliminado",
        repo_id=normalized_repo_id,
        cleared=cleared,
        deleted_counts=deleted_counts,
        warnings=warnings,
    )
