"""Modelos de datos de Pydantic para solicitudes, trabajos y objetos de recuperación."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _normalize_optional_text(value: Any) -> str | None | Any:
    """Normaliza placeholders de OpenAPI/Swagger en campos opcionales."""
    if value is None or not isinstance(value, str):
        return value

    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.lower() == "string":
        return None
    return cleaned


def utc_now() -> datetime:
    """Retorna fecha/hora actual en UTC con timezone explícito."""
    return datetime.now(UTC)


class JobStatus(str, Enum):
    """Estados del ciclo de vida admitidos para trabajos de ingesta."""

    queued = "queued"
    running = "running"
    partial = "partial"
    completed = "completed"
    failed = "failed"


class RepoAuthConfig(BaseModel):
    """Configuración explícita de autenticación Git para una ingesta."""

    deployment: Literal["auto", "cloud", "server", "data_center"] = Field(
        default="auto",
        description="Tipo de despliegue Git objetivo para resolver defaults.",
        examples=["auto", "cloud", "server"],
    )
    transport: Literal["auto", "https", "ssh"] = Field(
        default="auto",
        description="Transporte Git preferido para clonar el repositorio.",
        examples=["auto", "https", "ssh"],
    )
    method: Literal["auto", "ssh_key", "http_basic", "http_token"] = Field(
        default="auto",
        description="Método de autenticación Git solicitado.",
        examples=["auto", "ssh_key", "http_basic", "http_token"],
    )
    username: str | None = Field(
        default=None,
        description="Usuario opcional para autenticación HTTPS.",
    )
    secret: str | None = Field(
        default=None,
        description="Secreto opcional runtime para autenticación HTTPS.",
    )

    def has_explicit_values(self) -> bool:
        """Indica si el bloque auth contiene datos distintos a defaults."""
        return any(
            (
                self.deployment != "auto",
                self.transport != "auto",
                self.method != "auto",
                bool((self.username or "").strip()),
                bool((self.secret or "").strip()),
            )
        )

    def normalized_copy(self) -> "RepoAuthConfig":
        """Retorna una copia con strings saneados para consumo interno."""
        copy = self.model_copy(deep=True)
        copy.username = (copy.username or "").strip() or None
        copy.secret = (copy.secret or "").strip() or None
        return copy


class RepoIngestRequest(BaseModel):
    """Modelo de entrada para solicitudes de ingesta de repositorio."""

    provider: str = Field(
        default="github",
        description="Proveedor Git del repositorio a ingerir.",
        examples=["github"],
    )
    repo_url: str = Field(
        description="URL del repositorio remoto.",
        examples=[
            "https://github.com/macrozheng/mall.git",
            "git@bitbucket.org:workspace/proyecto.git",
        ],
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
    token: str | None = Field(
        default=None,
        description=(
            "Token legacy opcional para autenticación HTTPS. Se mantiene por "
            "compatibilidad y se mapea al bloque auth cuando aplica."
        ),
    )
    auth: RepoAuthConfig | None = Field(
        default=None,
        description=(
            "Configuración explícita de autenticación Git para soportar "
            "distintos providers y despliegues."
        ),
    )
    embedding_provider: str | None = Field(
        default=None,
        description="Proveedor de embeddings opcional para esta ingesta.",
        examples=["openai", "gemini", "vertex"],
    )
    embedding_model: str | None = Field(
        default=None,
        description="Modelo de embeddings opcional para esta ingesta.",
    )

    @field_validator(
        "commit",
        "token",
        "embedding_provider",
        "embedding_model",
        mode="before",
    )
    @classmethod
    def normalize_optional_text_fields(cls, value: Any) -> str | None | Any:
        """Convierte placeholders opcionales vacíos o Swagger a None."""
        return _normalize_optional_text(value)

    def resolved_auth(self) -> RepoAuthConfig:
        """Devuelve la configuración auth efectiva preservando compatibilidad."""
        provider = (self.provider or "github").strip().lower()
        auth = (
            self.auth.normalized_copy()
            if self.auth is not None
            else RepoAuthConfig()
        )

        legacy_token = (self.token or "").strip()
        if legacy_token and not auth.secret and provider == "github":
            if auth.transport == "auto":
                auth.transport = "https"
            if auth.method == "auto":
                auth.method = "http_token"
            auth.secret = legacy_token
            if not auth.username:
                auth.username = "x-access-token"

        return auth


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
    diagnostics: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Diagnósticos estructurados de ingesta (por ejemplo, métricas "
            "semánticas) cuando estén disponibles."
        ),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="Fecha/hora de creación del job (UTC).",
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
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
    embedding_provider: str | None = Field(
        default=None,
        description="Proveedor de embeddings opcional para vectorizar query.",
        examples=["openai", "gemini", "vertex"],
    )
    embedding_model: str | None = Field(
        default=None,
        description="Modelo de embeddings opcional para vectorizar query.",
    )
    llm_provider: str | None = Field(
        default=None,
        description="Proveedor LLM opcional para respuesta/verificación.",
        examples=["openai", "gemini", "vertex"],
    )
    answer_model: str | None = Field(
        default=None,
        description="Modelo answer opcional para la consulta.",
    )
    verifier_model: str | None = Field(
        default=None,
        description="Modelo verifier opcional para la consulta.",
    )


class RetrievalQueryRequest(BaseModel):
    """Modelo de entrada para consultas retrieval-only sin síntesis LLM."""

    repo_id: str = Field(description="Repositorio indexado objetivo.", examples=["mall"])
    query: str = Field(
        description="Pregunta en lenguaje natural para retrieval de evidencia.",
        examples=["donde esta la configuracion de neo4j"],
    )
    top_n: int = Field(
        default=60,
        ge=1,
        description="Cantidad de candidatos recuperados antes del reranking.",
    )
    top_k: int = Field(
        default=15,
        ge=1,
        description="Cantidad final tras reranking retornada como evidencia.",
    )
    embedding_provider: str | None = Field(
        default=None,
        description="Proveedor de embeddings opcional para vectorizar query.",
        examples=["openai", "gemini", "vertex"],
    )
    embedding_model: str | None = Field(
        default=None,
        description="Modelo de embeddings opcional para vectorizar query.",
    )
    include_context: bool = Field(
        default=False,
        description=(
            "Incluye el contexto ensamblado completo del pipeline en la respuesta."
        ),
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


class RetrievedChunk(BaseModel):
    """Fragmento recuperado y ranqueado sin síntesis de LLM."""

    id: str = Field(description="Identificador del chunk recuperado.")
    text: str = Field(description="Texto del fragmento recuperado.")
    score: float = Field(description="Score de relevancia del fragmento.")
    path: str = Field(description="Ruta del archivo fuente del fragmento.")
    start_line: int = Field(description="Línea inicial del fragmento fuente.")
    end_line: int = Field(description="Línea final del fragmento fuente.")
    kind: str = Field(default="code_chunk", description="Tipo de evidencia recuperada.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata adicional original del chunk recuperado.",
    )


class RetrievalStatistics(BaseModel):
    """Métricas agregadas de conteo para consultas retrieval-only."""

    total_before_rerank: int = Field(default=0, ge=0, description="Total recuperado antes de reranking.")
    total_after_rerank: int = Field(default=0, ge=0, description="Total retornado tras reranking.")
    graph_nodes_count: int = Field(default=0, ge=0, description="Nodos agregados por expansión de grafo.")


class RetrievalQueryResponse(BaseModel):
    """Modelo de salida para endpoint retrieval-only sin síntesis LLM."""

    mode: str = Field(default="retrieval_only", description="Modo de consulta ejecutado.")
    answer: str = Field(description="Resumen textual extractivo basado en evidencia recuperada.")
    chunks: list[RetrievedChunk] = Field(default_factory=list, description="Evidencia ranqueada recuperada.")
    citations: list[Citation] = Field(default_factory=list, description="Citas trazables asociadas a la evidencia.")
    statistics: RetrievalStatistics = Field(
        default_factory=RetrievalStatistics,
        description="Conteos agregados del pipeline retrieval-only.",
    )
    diagnostics: dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnóstico técnico de pipeline retrieval-only.",
    )
    context: str | None = Field(
        default=None,
        description="Contexto ensamblado completo cuando include_context=true.",
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


class RepoDeleteResponse(BaseModel):
    """Modelo de salida devuelto por el endpoint de borrado por repositorio."""

    message: str = Field(description="Mensaje general del resultado de borrado.")
    repo_id: str = Field(description="Repositorio solicitado para eliminación.")
    cleared: list[str] = Field(
        default_factory=list,
        description="Capas o recursos eliminados durante la operación.",
    )
    deleted_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Conteos de elementos eliminados por componente.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Advertencias no bloqueantes de la operación.",
    )


class RepoCatalogResponse(BaseModel):
    """Modelo de salida para identificadores de repositorio disponibles para consultas."""

    repo_ids: list[str] = Field(default_factory=list, description="Lista de repo_id disponibles para consulta.")


class ProviderModelCatalogResponse(BaseModel):
    """Respuesta de catálogo de modelos para provider/tipo solicitado."""

    provider: str = Field(description="Provider evaluado para discovery.")
    kind: str = Field(description="Tipo de modelos retornados (embedding o llm).")
    models: list[str] = Field(default_factory=list, description="Lista de modelos disponibles o fallback.")
    source: str = Field(description="Origen de datos: remote, cache o fallback.")
    warning: str | None = Field(default=None, description="Código de advertencia cuando aplica fallback o error.")


class RepoQueryStatusResponse(BaseModel):
    """Estado de disponibilidad de consulta para un repositorio específico."""

    repo_id: str = Field(description="Repositorio evaluado.")
    listed_in_catalog: bool = Field(description="Indica si el repo aparece en el catálogo /repos.")
    query_ready: bool = Field(description="Indica si el repo está listo para /query.")
    chroma_counts: dict[str, int | None] = Field(default_factory=dict, description="Conteos por colección Chroma (code_symbols, code_files, code_modules).")
    chroma_hnsw_space_configured: str | None = Field(
        default=None,
        description="Valor configurado de CHROMA_HNSW_SPACE.",
    )
    chroma_hnsw_space_detected: dict[str, str | None] = Field(
        default_factory=dict,
        description="Espacio HNSW detectado por colección Chroma.",
    )
    chroma_hnsw_space_compatible: bool | None = Field(
        default=None,
        description=(
            "Compatibilidad entre CHROMA_HNSW_SPACE configurado y el espacio "
            "detectado en colecciones existentes."
        ),
    )
    chroma_hnsw_space_mismatched_collections: list[str] = Field(
        default_factory=list,
        description="Colecciones Chroma desalineadas respecto al espacio configurado.",
    )
    bm25_loaded: bool = Field(description="Indica si BM25 está cargado en memoria para el repo.")
    graph_available: bool | None = Field(default=None, description="Disponibilidad de grafo para el repo (si pudo evaluarse).")
    last_embedding_provider: str | None = Field(
        default=None,
        description=(
            "Proveedor de embedding usado en la última ingesta conocida del repo."
        ),
    )
    last_embedding_model: str | None = Field(
        default=None,
        description=(
            "Modelo de embedding usado en la última ingesta conocida del repo."
        ),
    )
    embedding_compatible: bool | None = Field(
        default=None,
        description=(
            "Compatibilidad entre embedding de consulta y embedding de la "
            "última ingesta (None cuando no se puede evaluar)."
        ),
    )
    compatibility_reason: str | None = Field(
        default=None,
        description="Código breve que explica el resultado de compatibilidad.",
    )
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


SemanticRelationType = Literal["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"]


class SemanticRelation(BaseModel):
    """Relación semántica extraída entre símbolos o referencias externas."""

    repo_id: str
    source_symbol_id: str
    relation_type: SemanticRelationType
    target_symbol_id: str | None = None
    target_ref: str
    target_kind: str
    path: str
    line: int
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    language: str


class RetrievalChunk(BaseModel):
    """Fragmento devuelto de la recuperación de vector/BM25/gráfico."""

    id: str
    text: str
    score: float
    metadata: dict[str, Any]
