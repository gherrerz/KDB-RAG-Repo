"""Configuración de la aplicación cargada desde variables de entorno."""

import base64
import binascii
from functools import lru_cache
import json
import logging
from pathlib import Path
from typing import Literal
from urllib.parse import quote

_settings_log = logging.getLogger(__name__)

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from coderag.core.provider_model_catalog import normalize_provider_name
from coderag.core.vertex_ai import derive_vertex_location_from_base_url


ProviderName = Literal["openai", "gemini", "vertex"]
HnswSpaceName = Literal["l2", "cosine"]
IngestionExecutionMode = Literal["thread", "rq"]
VertexAuthMode = Literal["service_account"]
GitSshStrictHostKeyChecking = Literal["yes", "accept-new", "no"]
ChromaMode = Literal["embedded", "remote"]


class Settings(BaseSettings):
    """Configuraciones centralizadas para la configuración del tiempo de ejecución."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_image: str = Field(default="kdb-rag-api:local", alias="API_IMAGE")
    python_path: str = Field(default="/app/src", alias="PYTHONPATH")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    llm_provider: ProviderName = Field(default="vertex", alias="LLM_PROVIDER")
    llm_answer_model: str = Field(default="gemini-2.5-flash", alias="LLM_ANSWER_MODEL")
    llm_verifier_model: str = Field(default="gemini-2.5-flash", alias="LLM_VERIFIER_MODEL")
    llm_verify_enabled: bool = Field(default=False, alias="LLM_VERIFY_ENABLED")
    embedding_provider: ProviderName = Field(
        default="vertex",
        alias="EMBEDDING_PROVIDER",
    )
    embedding_model: str = Field(default="text-embedding-005", alias="EMBEDDING_MODEL")

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    vertex_ai_auth_mode: VertexAuthMode = Field(
        default="service_account",
        alias="VERTEX_AI_AUTH_MODE",
    )
    vertex_ai_service_account_json_b64: str = Field(
        default="",
        alias="VERTEX_AI_SERVICE_ACCOUNT_JSON_B64",
    )
    vertex_service_account_json_b64: str = Field(
        default="",
        alias="VERTEX_SERVICE_ACCOUNT_JSON_B64",
    )
    vertex_ai_project_id: str = Field(default="", alias="VERTEX_AI_PROJECT_ID")
    vertex_ai_location: str = Field(default="us-central1", alias="VERTEX_AI_LOCATION")
    vertex_api_base_url: str = Field(
        default="https://us-central1-aiplatform.googleapis.com",
        alias="VERTEX_API_BASE_URL",
    )
    vertex_api_version: str = Field(default="v1", alias="VERTEX_API_VERSION")
    vertex_generate_content_path_template: str = Field(
        default=(
            "/projects/{project}/locations/{location}/publishers/google/"
            "models/{model}:generateContent"
        ),
        alias="VERTEX_GENERATE_CONTENT_PATH_TEMPLATE",
    )
    vertex_predict_path_template: str = Field(
        default=(
            "/projects/{project}/locations/{location}/publishers/google/"
            "models/{model}:predict"
        ),
        alias="VERTEX_PREDICT_PATH_TEMPLATE",
    )
    vertex_models_path_template: str = Field(
        default=(
            "/projects/{project}/locations/{location}/publishers/google/models"
        ),
        alias="VERTEX_MODELS_PATH_TEMPLATE",
    )
    vertex_auth_token_url: str = Field(
        default="https://oauth2.googleapis.com/token",
        alias="VERTEX_AUTH_TOKEN_URL",
    )
    vertex_ai_labels_enabled: bool = Field(
        default=True,
        alias="VERTEX_AI_LABELS_ENABLED",
    )
    vertex_ai_label_service: str = Field(
        default="webspec-coipo",
        alias="VERTEX_AI_LABEL_SERVICE",
    )
    vertex_ai_label_service_account: str = Field(
        default="qa-anthos",
        alias="VERTEX_AI_LABEL_SERVICE_ACCOUNT",
    )
    vertex_ai_label_use_case_id: str = Field(
        default="tbd",
        alias="VERTEX_AI_LABEL_USE_CASE_ID",
    )
    vertex_ai_correlation_id_enabled: bool = Field(
        default=True,
        alias="VERTEX_AI_CORRELATION_ID_ENABLED",
    )
    # chroma_path sólo es relevante en CHROMA_MODE=embedded
    chroma_path: Path = Field(default=Path("/app/storage/chroma"), alias="CHROMA_PATH")
    chroma_hnsw_space: HnswSpaceName = Field(
        default="cosine",
        alias="CHROMA_HNSW_SPACE",
    )
    chroma_mode: ChromaMode = Field(default="remote", alias="CHROMA_MODE")
    chroma_host: str = Field(default="localhost", alias="CHROMA_HOST")
    chroma_port: int = Field(default=8000, alias="CHROMA_PORT")
    chroma_token: str = Field(default="", alias="CHROMA_TOKEN")
    chroma_username: str = Field(default="", alias="CHROMA_USERNAME")
    chroma_password: str = Field(default="", alias="CHROMA_PASSWORD")
    postgres_host: str = Field(default="", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="", alias="POSTGRES_DB")
    postgres_user: str = Field(default="", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_pool_size: int = Field(default=5, alias="POSTGRES_POOL_SIZE")
    postgres_pool_timeout: float = Field(default=30.0, alias="POSTGRES_POOL_TIMEOUT")
    lexical_fts_language: str = Field(default="english", alias="LEXICAL_FTS_LANGUAGE")
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
        default=Path("/app/storage/workspace"),
        alias="WORKSPACE_PATH",
    )
    retain_workspace_after_ingest: bool = Field(
        default=False,
        alias="RETAIN_WORKSPACE_AFTER_INGEST",
    )
    max_context_tokens: int = Field(default=8000, alias="MAX_CONTEXT_TOKENS")
    graph_hops: int = Field(default=2, alias="GRAPH_HOPS")
    query_max_seconds: float = Field(default=55.0, alias="QUERY_MAX_SECONDS")
    openai_timeout_seconds: float = Field(default=60, alias="OPENAI_TIMEOUT_SECONDS")
    ui_request_timeout_seconds: float = Field(
        default=90.0,
        alias="UI_REQUEST_TIMEOUT_SECONDS",
    )
    inventory_page_size: int = Field(default=80, alias="INVENTORY_PAGE_SIZE")
    inventory_max_page_size: int = Field(default=300, alias="INVENTORY_MAX_PAGE_SIZE")
    inventory_alias_limit: int = Field(default=8, alias="INVENTORY_ALIAS_LIMIT")
    inventory_entity_limit: int = Field(default=500, alias="INVENTORY_ENTITY_LIMIT")
    scan_max_file_size_bytes: int | None = Field(
        default=2000000,
        alias="SCAN_MAX_FILE_SIZE_BYTES",
    )
    scan_excluded_dirs: str = Field(
        default=".git,node_modules,dist,build,venv,.venv,__pycache__,.idea,.vscode,target,out,bin,obj,.gradle,.m2,.pytest_cache,.mypy_cache",
        alias="SCAN_EXCLUDED_DIRS",
    )
    scan_excluded_extensions: str = Field(
        default=".png,.jpg,.jpeg,.gif,.webp,.ico,.mp3,.mp4,.wav,.ogg,.pdf,.zip,.tar,.gz,.7z,.rar,.jar,.war,.ear,.class,.dll,.exe,.so,.dylib,.o,.a,.bin,.sqlite,.db",
        alias="SCAN_EXCLUDED_EXTENSIONS",
    )
    scan_excluded_files: str = Field(
        default=".gitignore,.env",
        alias="SCAN_EXCLUDED_FILES",
    )
    symbol_extractor_v2_enabled: bool = Field(
        default=True,
        alias="SYMBOL_EXTRACTOR_V2_ENABLED",
    )
    semantic_graph_enabled: bool = Field(
        default=True,
        alias="SEMANTIC_GRAPH_ENABLED",
    )
    semantic_graph_java_enabled: bool = Field(
        default=True,
        alias="SEMANTIC_GRAPH_JAVA_ENABLED",
    )
    semantic_graph_javascript_enabled: bool = Field(
        default=True,
        alias="SEMANTIC_GRAPH_JAVASCRIPT_ENABLED",
    )
    semantic_graph_typescript_enabled: bool = Field(
        default=True,
        alias="SEMANTIC_GRAPH_TYPESCRIPT_ENABLED",
    )
    semantic_graph_file_edges_enabled: bool = Field(
        default=True,
        alias="SEMANTIC_GRAPH_FILE_EDGES_ENABLED",
    )
    semantic_tsconfig_resolution_enabled: bool = Field(
        default=True,
        alias="SEMANTIC_TSCONFIG_RESOLUTION_ENABLED",
    )
    semantic_graph_query_enabled: bool = Field(
        default=True,
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
    health_check_openai: bool = Field(default=False, alias="HEALTH_CHECK_OPENAI")
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

    @model_validator(mode="after")
    def _validate_chroma_remote_auth(self) -> "Settings":
        """Valida combinaciones soportadas de autenticación remota de Chroma."""
        token = (self.chroma_token or "").strip()
        username = (self.chroma_username or "").strip()
        password = (self.chroma_password or "").strip()

        self.chroma_token = token
        self.chroma_username = username
        self.chroma_password = password

        has_basic = bool(username or password)
        if token and has_basic:
            raise ValueError(
                "CHROMA_TOKEN es mutuamente excluyente con "
                "CHROMA_USERNAME/CHROMA_PASSWORD."
            )
        if username and not password:
            raise ValueError(
                "CHROMA_PASSWORD es obligatorio cuando CHROMA_USERNAME está configurado."
            )
        if password and not username:
            raise ValueError(
                "CHROMA_USERNAME es obligatorio cuando CHROMA_PASSWORD está configurado."
            )
        return self

    @model_validator(mode="after")
    def _warn_weak_default_credentials(self) -> "Settings":
        """Emite advertencia cuando se detectan contraseñas débiles por defecto (CWE-798)."""
        weak_defaults = {
            "NEO4J_PASSWORD": (self.neo4j_password, "password"),
            "POSTGRES_PASSWORD": (self.postgres_password, "coderag"),
        }
        for env_var, (current, default_val) in weak_defaults.items():
            if current == default_val:
                _settings_log.warning(
                    "SECURITY: %s usa la contraseña por defecto '%s'. "
                    "Defina la variable de entorno con un valor seguro antes de "
                    "desplegar en producción.",
                    env_var,
                    default_val,
                )
        return self

    @model_validator(mode="after")
    def _normalize_postgres_settings(self) -> "Settings":
        """Normaliza el contrato de PostgreSQL basado en host/puerto."""
        self.postgres_host = (self.postgres_host or "").strip()
        self.postgres_db = (self.postgres_db or "").strip()
        self.postgres_user = (self.postgres_user or "").strip()
        self.postgres_password = (self.postgres_password or "").strip()

        if self.postgres_port <= 0:
            raise ValueError("POSTGRES_PORT debe ser un entero positivo.")
        return self

    def resolve_postgres_dsn(self) -> str:
        """Construye la DSN efectiva de PostgreSQL desde variables separadas."""
        host = (self.postgres_host or "").strip()
        if not host:
            return ""

        database = (self.postgres_db).strip()
        user = quote((self.postgres_user).strip(), safe="")
        password = quote(
            (self.postgres_password).strip(),
            safe="",
        )
        host_part = host
        if ":" in host and not host.startswith("["):
            host_part = f"[{host}]"
        db_part = quote(database, safe="")
        return (
            f"postgresql://{user}:{password}@{host_part}:"
            f"{self.postgres_port}/{db_part}"
        )

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
            return "gemini-2.5-flash"
        if normalized_provider == "vertex":
            return "gemini-2.5-flash"
        return "gemini-2.5-flash"

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
            return "gemini-2.5-flash"
        if normalized_provider == "vertex":
            return "gemini-2.5-flash"
        return "gemini-2.5-flash"

    def resolve_api_key(self, provider: ProviderName) -> str:
        """Obtiene la API key efectiva por proveedor."""
        normalized_provider = normalize_provider_name(provider)
        if normalized_provider == "gemini":
            return self.gemini_api_key
        if normalized_provider == "vertex":
            return ""
        return self.openai_api_key

    def resolve_vertex_credentials_reference(self) -> str:
        """Resuelve credencial Base64 canónica de Service Account para Vertex."""
        canonical_b64 = (self.vertex_service_account_json_b64 or "").strip()
        if canonical_b64:
            return canonical_b64
        return (self.vertex_ai_service_account_json_b64 or "").strip()

    def decode_vertex_service_account_b64(self) -> dict[str, object] | None:
        """Decodifica el JSON de Service Account desde Base64 cuando existe."""
        raw_b64 = self.resolve_vertex_credentials_reference()
        if not raw_b64:
            return None

        try:
            decoded_bytes = base64.b64decode(raw_b64, validate=True)
        except binascii.Error as exc:
            raise ValueError(
                "VERTEX service account JSON B64 no contiene Base64 válido."
            ) from exc

        try:
            decoded_text = decoded_bytes.decode("utf-8")
            payload = json.loads(decoded_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "VERTEX service account JSON B64 no contiene JSON válido."
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "VERTEX service account JSON B64 debe decodificar a un objeto JSON."
            )
        return payload

    def resolve_vertex_project_id(self) -> str:
        """Resuelve el project_id efectivo de Vertex priorizando el service account."""
        decoded_payload = self.decode_vertex_service_account_b64()
        if decoded_payload:
            project_id = str(decoded_payload.get("project_id") or "").strip()
            if project_id:
                return project_id
        return (self.vertex_ai_project_id or "").strip()

    def resolve_vertex_location(self) -> str:
        """Resuelve la location de Vertex desde base URL con fallback legacy."""
        derived_location = derive_vertex_location_from_base_url(
            self.vertex_api_base_url
        )
        if derived_location:
            return derived_location
        return (self.vertex_ai_location or "us-central1").strip() or "us-central1"

    def resolve_vertex_api_base_url(self) -> str:
        """Resuelve la base URL efectiva de Vertex AI."""
        return (self.vertex_api_base_url or "").strip()

    def vertex_ai_missing_reason(self) -> str:
        """Explica por qué Vertex AI no está completamente configurado."""
        if not self.resolve_vertex_project_id():
            return "missing_vertex_ai_api_key_or_project"
        raw_b64 = self.resolve_vertex_credentials_reference()
        if not raw_b64:
            return "missing_vertex_ai_api_key_or_project"
        try:
            decoded_payload = self.decode_vertex_service_account_b64()
        except ValueError:
            return "missing_vertex_ai_api_key_or_project"
        if not decoded_payload:
            return "missing_vertex_ai_api_key_or_project"
        if not self.resolve_vertex_api_base_url():
            return "missing_vertex_ai_api_key_or_project"
        if not self.resolve_vertex_location():
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


def resolve_postgres_dsn(settings: object) -> str:
    """Resuelve la DSN de Postgres desde Settings reales o doubles de prueba."""
    resolver = getattr(settings, "resolve_postgres_dsn", None)
    if callable(resolver):
        return str(resolver()).strip()

    host = str(getattr(settings, "postgres_host", "")).strip()
    if not host:
        return ""

    port = int(getattr(settings, "postgres_port", 5432) or 5432)
    if port <= 0:
        raise ValueError("POSTGRES_PORT debe ser un entero positivo.")

    database = str(getattr(settings, "postgres_db", "")).strip()
    user = str(getattr(settings, "postgres_user", "")).strip()
    password = str(
        getattr(settings, "postgres_password", "") 
    ).strip()
    password = password

    host_part = host
    if ":" in host and not host.startswith("["):
        host_part = f"[{host}]"

    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@"
        f"{host_part}:{port}/{quote(database, safe='')}"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la instancia de configuración singleton."""
    settings = Settings()
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    return settings
