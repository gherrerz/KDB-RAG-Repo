"""Modelos de datos de Pydantic para solicitudes, trabajos y objetos de recuperación."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Estados del ciclo de vida admitidos para trabajos de ingesta."""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class RepoIngestRequest(BaseModel):
    """Modelo de entrada para solicitudes de ingesta de repositorio."""

    provider: str = Field(default="github")
    repo_url: str
    token: str | None = None
    branch: str = "main"
    commit: str | None = None


class JobInfo(BaseModel):
    """Instantánea del estado actual de un trabajo de ingesta."""

    id: str
    status: JobStatus
    progress: float = 0.0
    logs: list[str] = Field(default_factory=list)
    repo_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class QueryRequest(BaseModel):
    """Modelo de entrada para preguntas de usuario en lenguaje natural."""

    repo_id: str
    query: str
    top_n: int = 60
    top_k: int = 15


class InventoryQueryRequest(BaseModel):
    """Modelo de entrada para consultas de inventario basadas en gráficos."""

    repo_id: str
    query: str
    page: int = 1
    page_size: int = 80


class Citation(BaseModel):
    """Metadatos de evidencia para cada afirmación respaldada en una respuesta."""

    path: str
    start_line: int
    end_line: int
    score: float
    reason: str


class QueryResponse(BaseModel):
    """Modelo de salida devuelto por el punto final de la consulta."""

    answer: str
    citations: list[Citation]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class InventoryItem(BaseModel):
    """Artículo de inventario estructurado descubierto en el gráfico del repositorio."""

    label: str
    path: str
    kind: str = "file"
    start_line: int = 1
    end_line: int = 1


class InventoryQueryResponse(BaseModel):
    """Modelo de salida devuelto por el punto final del inventario paginado."""

    answer: str
    target: str | None = None
    module_name: str | None = None
    total: int = 0
    page: int = 1
    page_size: int = 80
    items: list[InventoryItem] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class ResetResponse(BaseModel):
    """Modelo de salida devuelto por el endpoint de reinicio completo."""

    message: str
    cleared: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepoCatalogResponse(BaseModel):
    """Modelo de salida para identificadores de repositorio disponibles para consultas."""

    repo_ids: list[str] = Field(default_factory=list)


class StorageHealthItem(BaseModel):
    """Resultado de salud para un componente de almacenamiento del sistema."""

    name: str
    ok: bool
    critical: bool
    code: str
    message: str
    latency_ms: float
    details: dict[str, Any] = Field(default_factory=dict)


class StorageHealthResponse(BaseModel):
    """Estado consolidado de salud para componentes de almacenamiento del RAG."""

    ok: bool
    strict: bool
    checked_at: str
    context: str
    repo_id: str | None = None
    cached: bool = False
    failed_components: list[str] = Field(default_factory=list)
    items: list[StorageHealthItem] = Field(default_factory=list)


class ScannedFile(BaseModel):
    """Representa un archivo fuente descubierto en un análisis del repositorio."""

    path: str
    language: str
    content: str


class SymbolChunk(BaseModel):
    """Fragmento a nivel de símbolo extraído de un archivo fuente."""

    id: str
    repo_id: str
    path: str
    language: str
    symbol_name: str
    symbol_type: str
    start_line: int
    end_line: int
    snippet: str


class RetrievalChunk(BaseModel):
    """Fragmento devuelto de la recuperación de vector/BM25/gráfico."""

    id: str
    text: str
    score: float
    metadata: dict[str, Any]
