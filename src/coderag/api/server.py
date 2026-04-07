"""Servidor FastAPI para operaciones de ingesta y consulta."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from coderag.core.logging import configure_logging
from coderag.core.models import (
    InventoryQueryRequest,
    InventoryQueryResponse,
    JobInfo,
    ProviderModelCatalogResponse,
    QueryRequest,
    QueryResponse,
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
from coderag.jobs.worker import IngestionConflictError, JobManager
from coderag.llm.model_discovery import discover_models


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ejecuta validación estricta de storage durante el arranque de la API."""
    report = ensure_storage_ready(context="startup", force=True)
    app.state.storage_health = report
    yield


configure_logging()
app = FastAPI(
    title="RAG Hybrid Response Validator API",
    version="0.1.0",
    description=(
        "API para ingesta y consulta sobre repositorios de código usando "
        "retrieval híbrido (vector + BM25 + grafo).\n\n"
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
        return jobs.create_ingest_job(request)
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
    job = jobs.get_job(job_id)
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
                                "bm25_loaded": False,
                                "graph_available": None,
                                "warnings": [
                                    "No hay indice BM25 en memoria para repo 'mall'."
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

    listed_repo_ids = jobs.list_repo_ids()
    listed_in_catalog = request.repo_id in listed_repo_ids
    runtime_payload = jobs.get_repo_runtime(request.repo_id)
    readiness = get_repo_query_status(
        repo_id=request.repo_id,
        listed_in_catalog=listed_in_catalog,
        runtime_payload=runtime_payload,
        requested_embedding_provider=request.embedding_provider,
        requested_embedding_model=request.embedding_model,
    )
    if runtime_payload:
        readiness.update(runtime_payload)

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

    if not readiness["query_ready"]:
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

    listed_repo_ids = jobs.list_repo_ids()
    listed_in_catalog = request.repo_id in listed_repo_ids
    runtime_payload = jobs.get_repo_runtime(request.repo_id)
    readiness = get_repo_query_status(
        repo_id=request.repo_id,
        listed_in_catalog=listed_in_catalog,
        runtime_payload=runtime_payload,
        requested_embedding_provider=request.embedding_provider,
        requested_embedding_model=request.embedding_model,
    )
    if runtime_payload:
        readiness.update(runtime_payload)

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

    if not readiness["query_ready"]:
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
    """Devuelve los identificadores del repositorio actualmente disponibles para consultas."""
    return RepoCatalogResponse(repo_ids=jobs.list_repo_ids())


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
        "Chroma, carga BM25 y disponibilidad de grafo."
    ),
)
def repo_status(
    repo_id: str,
    requested_embedding_provider: str | None = None,
    requested_embedding_model: str | None = None,
) -> RepoQueryStatusResponse:
    """Devuelve estado de disponibilidad de consulta para un repositorio."""
    listed_repo_ids = jobs.list_repo_ids()
    runtime_payload = jobs.get_repo_runtime(repo_id)
    status_payload = get_repo_query_status(
        repo_id=repo_id,
        listed_in_catalog=repo_id in listed_repo_ids,
        runtime_payload=runtime_payload,
        requested_embedding_provider=requested_embedding_provider,
        requested_embedding_model=requested_embedding_model,
    )
    if runtime_payload:
        status_payload.update(runtime_payload)
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
    report = run_storage_preflight(context="health", force=True)
    app.state.storage_health = report
    return StorageHealthResponse(**report)


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
    try:
        cleared, warnings = jobs.reset_all_data()
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
        "Elimina un repositorio de Chroma, BM25, Neo4j, workspace y "
        "metadata SQLite. Rechaza la acción si hay jobs activos del mismo repo."
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

    try:
        cleared, warnings, deleted_counts = jobs.delete_repo(normalized_repo_id)
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
