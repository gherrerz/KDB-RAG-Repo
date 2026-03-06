"""FastAPI server for ingestion and query operations."""

from fastapi import FastAPI, HTTPException

from coderag.core.logging import configure_logging
from coderag.core.models import (
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
    """Create ingestion job and return initial job state."""
    return jobs.create_ingest_job(request)


@app.get("/jobs/{job_id}", response_model=JobInfo)
def get_job(job_id: str) -> JobInfo:
    """Return current status of ingestion job."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@app.post("/query", response_model=QueryResponse)
def query_repo(request: QueryRequest) -> QueryResponse:
    """Run hybrid query pipeline for one indexed repository."""
    from coderag.api.query_service import run_query

    return run_query(
        repo_id=request.repo_id,
        query=request.query,
        top_n=request.top_n,
        top_k=request.top_k,
    )


@app.get("/repos", response_model=RepoCatalogResponse)
def list_repos() -> RepoCatalogResponse:
    """Return repository identifiers currently available for querying."""
    return RepoCatalogResponse(repo_ids=jobs.list_repo_ids())


@app.post("/admin/reset", response_model=ResetResponse)
def reset_all_data() -> ResetResponse:
    """Reset all indexed data stores and local ingestion workspace."""
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
