"""Modelos de datos de Pydantic para solicitudes, trabajos y objetos de recuperación."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Estados del ciclo de vida admitidos para trabajos de ingesta."""

    queued = "queued"
    running = "running"
    partial = "partial"
    completed = "completed"
    failed = "failed"


class RepoIngestRequest(BaseModel):
    """Modelo de entrada para solicitudes de ingesta de repositorio."""

    provider: str = Field(
        default="github",
        description="Proveedor Git del repositorio a ingerir.",
        examples=["github"],
    )
    repo_url: str = Field(
        description="URL del repositorio remoto.",
        examples=["https://github.com/macrozheng/mall.git"],
    )
    token: str | None = Field(
        default=None,
        description="Token opcional para repositorios privados.",
    )
    branch: str = Field(
        default="main",
        description="Rama objetivo de ingesta.",
        examples=["main"],
    )
    commit: str | None = Field(
        default=None,
        description="Hash commit opcional para fijar una revisión específica.",
    )


class JobInfo(BaseModel):
    """Instantánea del estado actual de un trabajo de ingesta."""

    id: str = Field(description="Identificador único del job.")
    status: JobStatus = Field(description="Estado actual del ciclo de vida del job.")
    progress: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Progreso normalizado en rango [0.0, 1.0].",
    )
    logs: list[str] = Field(
        default_factory=list,
        description="Eventos y mensajes operativos del proceso.",
    )
    repo_id: str | None = Field(
        default=None,
        description="Identificador de repo resultante al completar ingesta.",
    )
    error: str | None = Field(
        default=None,
        description="Detalle de error si el job finaliza en estado failed.",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Fecha/hora de creación del job (UTC).",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Fecha/hora de última actualización del job (UTC).",
    )


class QueryRequest(BaseModel):
    """Modelo de entrada para preguntas de usuario en lenguaje natural."""

    repo_id: str = Field(description="Repositorio indexado objetivo.", examples=["mall"])
    query: str = Field(
        description="Pregunta en lenguaje natural.",
        examples=["cuales son todos los controller del modulo mall-admin?"],
    )
    top_n: int = Field(
        default=60,
        ge=1,
        description="Cantidad de candidatos recuperados antes del reranking.",
    )
    top_k: int = Field(
        default=15,
        ge=1,
        description="Cantidad final tras reranking usada para contexto/citas.",
    )


class InventoryQueryRequest(BaseModel):
    """Modelo de entrada para consultas de inventario basadas en gráficos."""

    repo_id: str = Field(description="Repositorio indexado objetivo.", examples=["mall"])
    query: str = Field(
        description="Consulta de inventario amplia (ejemplo: 'todos los modelos').",
        examples=["cuales son todos los modelos de mall-mbg"],
    )
    page: int = Field(default=1, ge=1, description="Número de página (1-indexed).")
    page_size: int = Field(
        default=80,
        ge=1,
        description="Tamaño de página solicitado para resultados de inventario.",
    )


class Citation(BaseModel):
    """Metadatos de evidencia para cada afirmación respaldada en una respuesta."""

    path: str = Field(description="Ruta del archivo fuente citado.")
    start_line: int = Field(description="Línea inicial de evidencia.")
    end_line: int = Field(description="Línea final de evidencia.")
    score: float = Field(description="Score de relevancia para la cita.")
    reason: str = Field(description="Origen de la cita (hybrid_rag_match o inventory_graph_match).")


class QueryResponse(BaseModel):
    """Modelo de salida devuelto por el punto final de la consulta."""

    answer: str = Field(description="Respuesta final al usuario.")
    citations: list[Citation] = Field(description="Evidencia trazable utilizada para responder.")
    diagnostics: dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnóstico técnico de pipeline (timings, fallback, conteos).",
    )


class InventoryItem(BaseModel):
    """Artículo de inventario estructurado descubierto en el gráfico del repositorio."""

    label: str = Field(description="Nombre visible del item de inventario.")
    path: str = Field(description="Ruta de archivo asociada al item.")
    kind: str = Field(default="file", description="Tipo de entidad inventariada.")
    start_line: int = Field(default=1, description="Línea inicial representativa.")
    end_line: int = Field(default=1, description="Línea final representativa.")


class InventoryQueryResponse(BaseModel):
    """Modelo de salida devuelto por el punto final del inventario paginado."""

    answer: str = Field(description="Respuesta textual del inventario solicitado.")
    target: str | None = Field(default=None, description="Entidad objetivo detectada en la consulta.")
    module_name: str | None = Field(default=None, description="Módulo detectado/resuelto para filtrar inventario.")
    total: int = Field(default=0, ge=0, description="Total de items disponibles antes de paginación.")
    page: int = Field(default=1, ge=1, description="Página aplicada en la respuesta.")
    page_size: int = Field(default=80, ge=1, description="Tamaño de página aplicado en la respuesta.")
    items: list[InventoryItem] = Field(default_factory=list, description="Lista paginada de items de inventario.")
    citations: list[Citation] = Field(default_factory=list, description="Citas asociadas al inventario retornado.")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Diagnóstico técnico del pipeline de inventario.")


class ResetResponse(BaseModel):
    """Modelo de salida devuelto por el endpoint de reinicio completo."""

    message: str = Field(description="Mensaje general de resultado del reset.")
    cleared: list[str] = Field(default_factory=list, description="Componentes/recursos limpiados.")
    warnings: list[str] = Field(default_factory=list, description="Advertencias no bloqueantes de la operación.")


class RepoCatalogResponse(BaseModel):
    """Modelo de salida para identificadores de repositorio disponibles para consultas."""

    repo_ids: list[str] = Field(default_factory=list, description="Lista de repo_id disponibles para consulta.")


class RepoQueryStatusResponse(BaseModel):
    """Estado de disponibilidad de consulta para un repositorio específico."""

    repo_id: str = Field(description="Repositorio evaluado.")
    listed_in_catalog: bool = Field(description="Indica si el repo aparece en el catálogo /repos.")
    query_ready: bool = Field(description="Indica si el repo está listo para /query.")
    chroma_counts: dict[str, int | None] = Field(default_factory=dict, description="Conteos por colección Chroma (code_symbols, code_files, code_modules).")
    bm25_loaded: bool = Field(description="Indica si BM25 está cargado en memoria para el repo.")
    graph_available: bool | None = Field(default=None, description="Disponibilidad de grafo para el repo (si pudo evaluarse).")
    warnings: list[str] = Field(default_factory=list, description="Advertencias de readiness no bloqueantes.")


class StorageHealthItem(BaseModel):
    """Resultado de salud para un componente de almacenamiento del sistema."""

    name: str = Field(description="Nombre del componente evaluado.")
    ok: bool = Field(description="Resultado de salud del componente.")
    critical: bool = Field(description="Si la falla del componente es crítica para operación.")
    code: str = Field(description="Código técnico de resultado/check.")
    message: str = Field(description="Mensaje descriptivo de estado del componente.")
    latency_ms: float = Field(description="Latencia del chequeo en milisegundos.")
    details: dict[str, Any] = Field(default_factory=dict, description="Detalle técnico adicional del componente.")


class StorageHealthResponse(BaseModel):
    """Estado consolidado de salud para componentes de almacenamiento del RAG."""

    ok: bool = Field(description="Estado global consolidado de salud.")
    strict: bool = Field(description="Indica si se aplicó modo estricto en la evaluación.")
    checked_at: str = Field(description="Fecha/hora ISO del chequeo.")
    context: str = Field(description="Contexto operacional del preflight (startup, query, ingest, etc.).")
    repo_id: str | None = Field(default=None, description="Repositorio evaluado, cuando aplica.")
    cached: bool = Field(default=False, description="Indica si el resultado proviene de caché.")
    failed_components: list[str] = Field(default_factory=list, description="Lista de componentes fallidos.")
    items: list[StorageHealthItem] = Field(default_factory=list, description="Detalle de salud por componente.")


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
