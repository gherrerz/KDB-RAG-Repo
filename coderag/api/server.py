"""Servidor FastAPI para operaciones de ingesta y consulta."""

from fastapi import FastAPI, HTTPException

from coderag.core.logging import configure_logging
from coderag.core.models import (
    InventoryQueryRequest,
    InventoryQueryResponse,
    JobInfo,
    QueryRequest,
    QueryResponse,
    RepoCatalogResponse,
    RepoIngestRequest,
    ResetResponse,
)
from coderag.jobs.worker import JobManager

configure_logging()
app = FastAPI(title="RAG Hybrid Response Validator API", version="0.1.0")
jobs = JobManager()


@app.post("/repos/ingest", response_model=JobInfo)
def ingest_repo(request: RepoIngestRequest) -> JobInfo:
    """Cree un trabajo de ingesta y devuelva el estado inicial del trabajo."""
    return jobs.create_ingest_job(request)


@app.get("/jobs/{job_id}", response_model=JobInfo)
def get_job(job_id: str) -> JobInfo:
    """Devuelve el estado actual del trabajo de ingesta."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@app.post("/query", response_model=QueryResponse)
def query_repo(request: QueryRequest) -> QueryResponse:
    """Ejecute una canalización de consultas híbrida para un repositorio indexado."""
    from coderag.api.query_service import run_query

    return run_query(
        repo_id=request.repo_id,
        query=request.query,
        top_n=request.top_n,
        top_k=request.top_k,
    )


@app.post("/inventory/query", response_model=InventoryQueryResponse)
def query_inventory(request: InventoryQueryRequest) -> InventoryQueryResponse:
    """Ejecute una consulta de inventario paginado primero en el gráfico para obtener intenciones de lista amplia."""
    from coderag.api.query_service import run_inventory_query

    return run_inventory_query(
        repo_id=request.repo_id,
        query=request.query,
        page=request.page,
        page_size=request.page_size,
    )


@app.get("/repos", response_model=RepoCatalogResponse)
def list_repos() -> RepoCatalogResponse:
    """Devuelve los identificadores del repositorio actualmente disponibles para consultas."""
    return RepoCatalogResponse(repo_ids=jobs.list_repo_ids())


@app.post("/admin/reset", response_model=ResetResponse)
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
