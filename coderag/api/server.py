"""FastAPI server for ingestion and query operations."""

from fastapi import FastAPI, HTTPException

from coderag.core.logging import configure_logging
from coderag.core.models import JobInfo, QueryRequest, QueryResponse, RepoIngestRequest
from coderag.jobs.worker import JobManager

configure_logging()
app = FastAPI(title="CodeRAG Studio API", version="0.1.0")
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
