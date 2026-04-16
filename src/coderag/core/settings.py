"""Configuración de la aplicación cargada desde variables de entorno."""

import base64
import binascii
from functools import lru_cache
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from coderag.core.provider_model_catalog import normalize_provider_name


ProviderName = Literal["openai", "gemini", "vertex", "vertex_ai"]
HnswSpaceName = Literal["l2", "cosine"]
IngestionExecutionMode = Literal["thread", "rq"]
VertexAuthMode = Literal["service_account"]
GitSshStrictHostKeyChecking = Literal["yes", "accept-new", "no"]


class Settings(BaseSettings):
    """Configuraciones centralizadas para la configuración del tiempo de ejecución."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    llm_provider: ProviderName = Field(default="vertex", alias="LLM_PROVIDER")
    llm_answer_model: str = Field(default="", alias="LLM_ANSWER_MODEL")
    llm_verifier_model: str = Field(default="", alias="LLM_VERIFIER_MODEL")
    llm_verify_enabled: bool = Field(default=True, alias="LLM_VERIFY_ENABLED")
    embedding_provider: ProviderName = Field(
        default="vertex",
        alias="EMBEDDING_PROVIDER",
    )
    embedding_model: str = Field(default="", alias="EMBEDDING_MODEL")

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    vertex_ai_auth_mode: VertexAuthMode = Field(
        default="service_account",
        alias="VERTEX_AI_AUTH_MODE",
    )
    vertex_ai_service_account_json_b64: str = Field(
        default="",
        alias="VERTEX_AI_SERVICE_ACCOUNT_JSON_B64",
    )
    vertex_ai_project_id: str = Field(default="", alias="VERTEX_AI_PROJECT_ID")
    vertex_ai_location: str = Field(default="us-central1", alias="VERTEX_AI_LOCATION")
    vertex_ai_labels_enabled: bool = Field(
        default=True,
        alias="VERTEX_AI_LABELS_ENABLED",
    )
    vertex_ai_label_service: str = Field(
        default="kdb-rag",
        alias="VERTEX_AI_LABEL_SERVICE",
    )
    vertex_ai_label_service_account: str = Field(
        default="",
        alias="VERTEX_AI_LABEL_SERVICE_ACCOUNT",
    )
    vertex_ai_label_use_case_id: str = Field(
        default="rag_query",
        alias="VERTEX_AI_LABEL_USE_CASE_ID",
    )
    vertex_ai_correlation_id_enabled: bool = Field(
        default=True,
        alias="VERTEX_AI_CORRELATION_ID_ENABLED",
    )
    chroma_path: Path = Field(default=Path("./storage/chroma"), alias="CHROMA_PATH")
    chroma_hnsw_space: HnswSpaceName = Field(
        default="cosine",
        alias="CHROMA_HNSW_SPACE",
    )
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="password", alias="NEO4J_PASSWORD")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    ingestion_execution_mode: IngestionExecutionMode = Field(
        default="thread",
        alias="INGESTION_EXECUTION_MODE",
    )
    ingestion_queue_name: str = Field(
        default="ingestion",
        alias="INGESTION_QUEUE_NAME",
    )
    ingestion_job_timeout_seconds: int = Field(
        default=7200,
        alias="INGESTION_JOB_TIMEOUT_SECONDS",
    )
    ingestion_result_ttl_seconds: int = Field(
        default=86400,
        alias="INGESTION_RESULT_TTL_SECONDS",
    )
    ingestion_failure_ttl_seconds: int = Field(
        default=604800,
        alias="INGESTION_FAILURE_TTL_SECONDS",
    )
    ingestion_retry_max: int = Field(
        default=3,
        alias="INGESTION_RETRY_MAX",
    )
    ingestion_retry_intervals: str = Field(
        default="30,120,300",
        alias="INGESTION_RETRY_INTERVALS",
    )
    ingestion_retry_transient_only: bool = Field(
        default=True,
        alias="INGESTION_RETRY_TRANSIENT_ONLY",
    )
    ingestion_enqueue_lock_seconds: int = Field(
        default=30,
        alias="INGESTION_ENQUEUE_LOCK_SECONDS",
    )
    ingestion_enqueue_lock_wait_seconds: int = Field(
        default=5,
        alias="INGESTION_ENQUEUE_LOCK_WAIT_SECONDS",
    )
    git_ssh_key_content: str = Field(
        default="",
        alias="GIT_SSH_KEY_CONTENT",
    )
    git_ssh_key_content_b64: str = Field(
        default="",
        alias="GIT_SSH_KEY_CONTENT_B64",
    )
    git_ssh_known_hosts_content: str = Field(
        default="",
        alias="GIT_SSH_KNOWN_HOSTS_CONTENT",
    )
    git_ssh_known_hosts_content_b64: str = Field(
        default="",
        alias="GIT_SSH_KNOWN_HOSTS_CONTENT_B64",
    )
    git_ssh_strict_host_key_checking: GitSshStrictHostKeyChecking = Field(
        default="yes",
        alias="GIT_SSH_STRICT_HOST_KEY_CHECKING",
    )
    workspace_path: Path = Field(
        default=Path("./storage/workspace"),
        alias="WORKSPACE_PATH",
    )
    max_context_tokens: int = Field(default=8000, alias="MAX_CONTEXT_TOKENS")
    graph_hops: int = Field(default=2, alias="GRAPH_HOPS")
    query_max_seconds: float = Field(default=55.0, alias="QUERY_MAX_SECONDS")
    openai_timeout_seconds: float = Field(default=20.0, alias="OPENAI_TIMEOUT_SECONDS")
    ui_request_timeout_seconds: float = Field(
        default=90.0,
        alias="UI_REQUEST_TIMEOUT_SECONDS",
    )
    inventory_page_size: int = Field(default=80, alias="INVENTORY_PAGE_SIZE")
    inventory_max_page_size: int = Field(default=300, alias="INVENTORY_MAX_PAGE_SIZE")
    inventory_alias_limit: int = Field(default=8, alias="INVENTORY_ALIAS_LIMIT")
    inventory_entity_limit: int = Field(default=500, alias="INVENTORY_ENTITY_LIMIT")
    scan_max_file_size_bytes: int | None = Field(
        default=None,
        alias="SCAN_MAX_FILE_SIZE_BYTES",
    )
    scan_excluded_dirs: str = Field(
        default="",
        alias="SCAN_EXCLUDED_DIRS",
    )
    scan_excluded_extensions: str = Field(
        default="",
        alias="SCAN_EXCLUDED_EXTENSIONS",
    )
    scan_excluded_files: str = Field(
        default="",
        alias="SCAN_EXCLUDED_FILES",
    )
    symbol_extractor_v2_enabled: bool = Field(
        default=True,
        alias="SYMBOL_EXTRACTOR_V2_ENABLED",
    )
    semantic_graph_enabled: bool = Field(
        default=False,
        alias="SEMANTIC_GRAPH_ENABLED",
    )
    semantic_graph_java_enabled: bool = Field(
        default=False,
        alias="SEMANTIC_GRAPH_JAVA_ENABLED",
    )
    semantic_graph_typescript_enabled: bool = Field(
        default=False,
        alias="SEMANTIC_GRAPH_TYPESCRIPT_ENABLED",
    )
    semantic_graph_query_enabled: bool = Field(
        default=False,
        alias="SEMANTIC_GRAPH_QUERY_ENABLED",
    )
    semantic_relation_types: str = Field(
        default="CALLS,IMPORTS,EXTENDS,IMPLEMENTS",
        alias="SEMANTIC_RELATION_TYPES",
    )
    semantic_graph_query_max_edges: int = Field(
        default=400,
        alias="SEMANTIC_GRAPH_QUERY_MAX_EDGES",
    )
    semantic_graph_query_max_nodes: int = Field(
        default=200,
        alias="SEMANTIC_GRAPH_QUERY_MAX_NODES",
    )
    semantic_graph_query_max_ms: float = Field(
        default=120.0,
        alias="SEMANTIC_GRAPH_QUERY_MAX_MS",
    )
    semantic_relation_weights: str = Field(
        default="CALLS:1.0,IMPORTS:0.7,EXTENDS:1.1,IMPLEMENTS:1.0",
        alias="SEMANTIC_RELATION_WEIGHTS",
    )
    semantic_graph_query_fallback_to_structural: bool = Field(
        default=True,
        alias="SEMANTIC_GRAPH_QUERY_FALLBACK_TO_STRUCTURAL",
    )
    health_check_strict: bool = Field(default=True, alias="HEALTH_CHECK_STRICT")
    health_check_timeout_seconds: float = Field(
        default=5.0,
        alias="HEALTH_CHECK_TIMEOUT_SECONDS",
    )
    health_check_ttl_seconds: float = Field(
        default=10.0,
        alias="HEALTH_CHECK_TTL_SECONDS",
    )
    health_check_openai: bool = Field(default=True, alias="HEALTH_CHECK_OPENAI")
    health_check_redis: bool = Field(default=False, alias="HEALTH_CHECK_REDIS")
    discovery_timeout_seconds: float = Field(
        default=8.0,
        alias="MODEL_DISCOVERY_TIMEOUT_SECONDS",
    )
    discovery_cache_ttl_seconds: int = Field(
        default=3600,
        alias="MODEL_DISCOVERY_CACHE_TTL_SECONDS",
    )
    discovery_max_results: int = Field(
        default=80,
        alias="MODEL_DISCOVERY_MAX_RESULTS",
    )
    discovery_gemini_sdk_enabled: bool = Field(
        default=True,
        alias="MODEL_DISCOVERY_GEMINI_SDK_ENABLED",
    )

    @field_validator("chroma_hnsw_space", mode="before")
    @classmethod
    def _validate_chroma_hnsw_space(cls, value: object) -> str:
        """Valida y normaliza el espacio HNSW soportado por Chroma."""
        normalized = str(value or "").strip().lower()
        if normalized not in {"l2", "cosine"}:
            raise ValueError(
                "CHROMA_HNSW_SPACE debe ser 'l2' o 'cosine' "
                f"(valor recibido: {value!r})."
            )
        return normalized

    def resolve_embedding_provider(self, override: str | None = None) -> ProviderName:
        """Resuelve el proveedor de embeddings con prioridad override > env."""
        provider = (override or self.embedding_provider or "vertex").strip().lower()
        normalized = normalize_provider_name(provider)
        if normalized in {"openai", "gemini", "vertex"}:
            return normalized  # type: ignore[return-value]
        return "vertex"

    def resolve_chroma_hnsw_space(
        self,
        override: str | None = None,
    ) -> HnswSpaceName:
        """Resuelve el espacio HNSW de Chroma con prioridad override > env."""
        candidate = (override or self.chroma_hnsw_space or "cosine").strip().lower()
        if candidate in {"l2", "cosine"}:
            return candidate  # type: ignore[return-value]
        return "cosine"

    def resolve_semantic_relation_types(
        self,
        override: str | None = None,
    ) -> list[str]:
        """Resuelve tipos de relación semántica válidos para query expansion."""
        raw_value = (override or self.semantic_relation_types or "").strip()
        allowed = {"CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"}
        values: list[str] = []
        seen: set[str] = set()
        for token in raw_value.split(","):
            normalized = token.strip().upper()
            if not normalized or normalized not in allowed:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
        return values

    def resolve_semantic_relation_weights(
        self,
        override: str | None = None,
    ) -> dict[str, float]:
        """Resuelve pesos por tipo de relación semántica para scoring en query."""
        defaults = {
            "CALLS": 1.0,
            "IMPORTS": 0.7,
            "EXTENDS": 1.1,
            "IMPLEMENTS": 1.0,
        }
        raw_value = (override or self.semantic_relation_weights or "").strip()
        if not raw_value:
            return defaults

        parsed = dict(defaults)
        allowed = set(defaults)
        for token in raw_value.split(","):
            entry = token.strip()
            if not entry or ":" not in entry:
                continue
            relation_type, raw_weight = entry.split(":", maxsplit=1)
            normalized_type = relation_type.strip().upper()
            if normalized_type not in allowed:
                continue
            try:
                weight = float(raw_weight.strip())
            except ValueError:
                continue
            if weight <= 0:
                continue
            parsed[normalized_type] = weight
        return parsed

    def resolve_embedding_model(self, provider: ProviderName, override: str | None = None) -> str:
        """Resuelve modelo de embeddings con fallback por provider."""
        normalized_provider = normalize_provider_name(provider)
        if override and override.strip():
            return override.strip()
        if self.embedding_model.strip():
            return self.embedding_model.strip()
        if normalized_provider == "openai":
            return "text-embedding-3-small"
        if normalized_provider == "gemini":
            return "text-embedding-004"
        if normalized_provider == "vertex":
            return "text-embedding-005"
        return "text-embedding-005"

    def resolve_llm_provider(self, override: str | None = None) -> ProviderName:
        """Resuelve el proveedor LLM con prioridad override > env."""
        provider = (override or self.llm_provider or "vertex").strip().lower()
        normalized = normalize_provider_name(provider)
        if normalized in {"openai", "gemini", "vertex"}:
            return normalized  # type: ignore[return-value]
        return "vertex"

    def resolve_ingestion_retry_intervals(self) -> list[int]:
        """Resuelve intervalos válidos de reintento para jobs de ingesta."""
        raw_value = (self.ingestion_retry_intervals or "").strip()
        if not raw_value:
            return []

        intervals: list[int] = []
        for token in raw_value.split(","):
            piece = token.strip()
            if not piece:
                continue
            try:
                seconds = int(piece)
            except ValueError:
                continue
            if seconds <= 0:
                continue
            intervals.append(seconds)
        return intervals

    def resolve_answer_model(self, provider: ProviderName, override: str | None = None) -> str:
        """Resuelve el modelo answer con fallback a configuración actual."""
        normalized_provider = normalize_provider_name(provider)
        if override and override.strip():
            return override.strip()
        if self.llm_answer_model.strip():
            return self.llm_answer_model.strip()
        if normalized_provider == "openai":
            return "gpt-4.1-mini"
        if normalized_provider == "gemini":
            return "gemini-2.0-flash"
        if normalized_provider == "vertex":
            return "gemini-2.0-flash"
        return "gemini-2.0-flash"

    def resolve_verifier_model(self, provider: ProviderName, override: str | None = None) -> str:
        """Resuelve el modelo verifier con fallback a configuración actual."""
        normalized_provider = normalize_provider_name(provider)
        if override and override.strip():
            return override.strip()
        if self.llm_verifier_model.strip():
            return self.llm_verifier_model.strip()
        if normalized_provider == "openai":
            return "gpt-4.1-mini"
        if normalized_provider == "gemini":
            return "gemini-2.0-flash"
        if normalized_provider == "vertex":
            return "gemini-2.0-flash"
        return "gemini-2.0-flash"

    def resolve_api_key(self, provider: ProviderName) -> str:
        """Obtiene la API key efectiva por proveedor."""
        normalized_provider = normalize_provider_name(provider)
        if normalized_provider == "gemini":
            return self.gemini_api_key
        if normalized_provider == "vertex":
            return ""
        return self.openai_api_key

    def decode_vertex_service_account_b64(self) -> dict[str, object] | None:
        """Decodifica el JSON de Service Account desde Base64 cuando existe."""
        raw_b64 = (self.vertex_ai_service_account_json_b64 or "").strip()
        if not raw_b64:
            return None

        try:
            decoded_bytes = base64.b64decode(raw_b64, validate=True)
        except binascii.Error as exc:
            raise ValueError(
                "VERTEX_AI_SERVICE_ACCOUNT_JSON_B64 no contiene Base64 válido."
            ) from exc

        try:
            decoded_text = decoded_bytes.decode("utf-8")
            payload = json.loads(decoded_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "VERTEX_AI_SERVICE_ACCOUNT_JSON_B64 no contiene JSON válido."
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "VERTEX_AI_SERVICE_ACCOUNT_JSON_B64 debe decodificar a un objeto JSON."
            )
        return payload

    def resolve_vertex_credentials_reference(self) -> str:
        """Resuelve credencial Base64 de Service Account para Vertex."""
        raw_b64 = (self.vertex_ai_service_account_json_b64 or "").strip()
        return raw_b64

    def vertex_ai_missing_reason(self) -> str:
        """Explica por qué Vertex AI no está completamente configurado."""
        if not self.vertex_ai_project_id:
            return "missing_vertex_ai_api_key_or_project"
        raw_b64 = (self.vertex_ai_service_account_json_b64 or "").strip()
        if not raw_b64:
            return "missing_vertex_ai_api_key_or_project"
        try:
            decoded_payload = self.decode_vertex_service_account_b64()
        except ValueError:
            return "missing_vertex_ai_api_key_or_project"
        if not decoded_payload:
            return "missing_vertex_ai_api_key_or_project"
        return "ok"

    def is_vertex_ai_configured(self) -> bool:
        """Valida si Vertex AI tiene credenciales de SA y proyecto mínimos."""
        return self.vertex_ai_missing_reason() == "ok"

    def embedding_provider_capabilities(self, provider: ProviderName) -> dict[str, str | bool]:
        """Devuelve capacidades/configuración del provider de embeddings."""
        normalized_provider = normalize_provider_name(provider)
        if normalized_provider == "openai":
            configured = bool(self.openai_api_key)
            reason = "ok" if configured else "missing_openai_api_key"
            return {
                "provider": normalized_provider,
                "supported": True,
                "configured": configured,
                "reason": reason,
            }
        if normalized_provider == "gemini":
            configured = bool(self.gemini_api_key)
            reason = "ok" if configured else "missing_gemini_api_key"
            return {
                "provider": normalized_provider,
                "supported": True,
                "configured": configured,
                "reason": reason,
            }
        if normalized_provider == "vertex":
            configured = self.is_vertex_ai_configured()
            reason = "ok" if configured else self.vertex_ai_missing_reason()
            return {
                "provider": normalized_provider,
                "supported": True,
                "configured": configured,
                "reason": reason,
            }
        return {
            "provider": normalized_provider,
            "supported": False,
            "configured": False,
            "reason": "provider_without_embedding_backend",
        }

    def llm_provider_capabilities(self, provider: ProviderName) -> dict[str, str | bool]:
        """Devuelve capacidades/configuración del provider LLM."""
        normalized_provider = normalize_provider_name(provider)
        if normalized_provider == "openai":
            configured = bool(self.openai_api_key)
            reason = "ok" if configured else "missing_openai_api_key"
            return {
                "provider": normalized_provider,
                "supported": True,
                "configured": configured,
                "answer": True,
                "verify": True,
                "reason": reason,
            }
        if normalized_provider == "gemini":
            configured = bool(self.gemini_api_key)
            reason = "ok" if configured else "missing_gemini_api_key"
            return {
                "provider": normalized_provider,
                "supported": True,
                "configured": configured,
                "answer": True,
                "verify": True,
                "reason": reason,
            }
        configured = self.is_vertex_ai_configured()
        reason = "ok" if configured else self.vertex_ai_missing_reason()
        return {
            "provider": normalized_provider,
            "supported": True,
            "configured": configured,
            "answer": True,
            "verify": True,
            "reason": reason,
        }

    def is_verify_enabled(self) -> bool:
        """Devuelve si la verificación LLM está habilitada."""
        return bool(self.llm_verify_enabled)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la instancia de configuración singleton."""
    settings = Settings()
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    return settings
