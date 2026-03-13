"""Servidor FastAPI para operaciones de ingesta y consulta."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from coderag.core.logging import configure_logging
from coderag.core.models import (
    InventoryQueryRequest,
    InventoryQueryResponse,
    JobInfo,
    QueryRequest,
    QueryResponse,
    RepoCatalogResponse,
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
from coderag.jobs.worker import JobManager


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
    return jobs.create_ingest_job(request)


@app.get(
    "/jobs/{job_id}",
    response_model=JobInfo,
    tags=["Ingesta"],
    summary="Consultar estado de job",
    description="Obtiene estado, progreso y logs del job de ingesta.",
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
def get_job(job_id: str) -> JobInfo:
    """Devuelve el estado actual del trabajo de ingesta."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


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
    readiness = get_repo_query_status(
        repo_id=request.repo_id,
        listed_in_catalog=listed_in_catalog,
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
    "/repos/{repo_id}/status",
    response_model=RepoQueryStatusResponse,
    tags=["Catalogo"],
    summary="Estado de readiness por repositorio",
    description=(
        "Evalúa si un repo está listo para /query, incluyendo conteos en "
        "Chroma, carga BM25 y disponibilidad de grafo."
    ),
)
def repo_status(repo_id: str) -> RepoQueryStatusResponse:
    """Devuelve estado de disponibilidad de consulta para un repositorio."""
    listed_repo_ids = jobs.list_repo_ids()
    status_payload = get_repo_query_status(
        repo_id=repo_id,
        listed_in_catalog=repo_id in listed_repo_ids,
    )
    return RepoQueryStatusResponse(**status_payload)


@app.get(
    "/health/storage",
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
