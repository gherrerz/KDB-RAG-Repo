"""Configuración de la aplicación cargada desde variables de entorno."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ProviderName = Literal["openai", "anthropic", "gemini", "vertex_ai"]


class Settings(BaseSettings):
    """Configuraciones centralizadas para la configuración del tiempo de ejecución."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )
    openai_answer_model: str = Field(
        default="gpt-4.1-mini",
        alias="OPENAI_ANSWER_MODEL",
    )
    openai_verifier_model: str = Field(
        default="gpt-4.1-mini",
        alias="OPENAI_VERIFIER_MODEL",
    )
    openai_verify_enabled: bool = Field(
        default=True,
        alias="OPENAI_VERIFY_ENABLED",
    )
    llm_provider: ProviderName = Field(default="openai", alias="LLM_PROVIDER")
    llm_answer_model: str = Field(default="", alias="LLM_ANSWER_MODEL")
    llm_verifier_model: str = Field(default="", alias="LLM_VERIFIER_MODEL")
    llm_verify_enabled: bool = Field(default=True, alias="LLM_VERIFY_ENABLED")
    embedding_provider: ProviderName = Field(
        default="openai",
        alias="EMBEDDING_PROVIDER",
    )
    embedding_model: str = Field(default="", alias="EMBEDDING_MODEL")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    vertex_ai_api_key: str = Field(default="", alias="VERTEX_AI_API_KEY")
    vertex_ai_project_id: str = Field(default="", alias="VERTEX_AI_PROJECT_ID")
    vertex_ai_location: str = Field(default="us-central1", alias="VERTEX_AI_LOCATION")
    chroma_path: Path = Field(default=Path("./storage/chroma"), alias="CHROMA_PATH")
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="password", alias="NEO4J_PASSWORD")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
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

    def resolve_embedding_provider(self, override: str | None = None) -> ProviderName:
        """Resuelve el proveedor de embeddings con prioridad override > env."""
        provider = (override or self.embedding_provider or "openai").strip().lower()
        if provider in {"openai", "anthropic", "gemini", "vertex_ai"}:
            return provider  # type: ignore[return-value]
        return "openai"

    def resolve_embedding_model(self, provider: ProviderName, override: str | None = None) -> str:
        """Resuelve el modelo de embeddings manteniendo fallback legado OpenAI."""
        if override and override.strip():
            return override.strip()
        if self.embedding_model.strip():
            return self.embedding_model.strip()
        if provider == "gemini":
            return "text-embedding-004"
        if provider == "vertex_ai":
            return "text-embedding-005"
        return self.openai_embedding_model

    def resolve_llm_provider(self, override: str | None = None) -> ProviderName:
        """Resuelve el proveedor LLM con prioridad override > env."""
        provider = (override or self.llm_provider or "openai").strip().lower()
        if provider in {"openai", "anthropic", "gemini", "vertex_ai"}:
            return provider  # type: ignore[return-value]
        return "openai"

    def resolve_answer_model(self, provider: ProviderName, override: str | None = None) -> str:
        """Resuelve el modelo answer con fallback a configuración actual."""
        if override and override.strip():
            return override.strip()
        if self.llm_answer_model.strip():
            return self.llm_answer_model.strip()
        if provider == "anthropic":
            return "claude-3-5-sonnet-20241022"
        if provider == "gemini":
            return "gemini-2.0-flash"
        if provider == "vertex_ai":
            return "gemini-2.0-flash"
        return self.openai_answer_model

    def resolve_verifier_model(self, provider: ProviderName, override: str | None = None) -> str:
        """Resuelve el modelo verifier con fallback a configuración actual."""
        if override and override.strip():
            return override.strip()
        if self.llm_verifier_model.strip():
            return self.llm_verifier_model.strip()
        if provider == "anthropic":
            return "claude-3-5-sonnet-20241022"
        if provider == "gemini":
            return "gemini-2.0-flash"
        if provider == "vertex_ai":
            return "gemini-2.0-flash"
        return self.openai_verifier_model

    def resolve_api_key(self, provider: ProviderName) -> str:
        """Obtiene la API key efectiva por proveedor con fallback legacy."""
        if provider == "anthropic":
            return self.anthropic_api_key
        if provider == "gemini":
            return self.gemini_api_key
        if provider == "vertex_ai":
            return self.vertex_ai_api_key
        return self.openai_api_key

    def is_vertex_ai_configured(self) -> bool:
        """Valida si Vertex AI tiene credenciales y proyecto mínimos."""
        return bool(self.vertex_ai_api_key and self.vertex_ai_project_id)

    def embedding_provider_capabilities(self, provider: ProviderName) -> dict[str, str | bool]:
        """Devuelve capacidades/configuración del provider de embeddings."""
        if provider == "openai":
            configured = bool(self.openai_api_key)
            reason = "ok" if configured else "missing_openai_api_key"
            return {"provider": provider, "supported": True, "configured": configured, "reason": reason}
        if provider == "gemini":
            configured = bool(self.gemini_api_key)
            reason = "ok" if configured else "missing_gemini_api_key"
            return {"provider": provider, "supported": True, "configured": configured, "reason": reason}
        if provider == "vertex_ai":
            configured = self.is_vertex_ai_configured()
            reason = "ok" if configured else "missing_vertex_ai_api_key_or_project"
            return {"provider": provider, "supported": True, "configured": configured, "reason": reason}
        return {
            "provider": provider,
            "supported": False,
            "configured": False,
            "reason": "provider_without_embedding_backend",
        }

    def llm_provider_capabilities(self, provider: ProviderName) -> dict[str, str | bool]:
        """Devuelve capacidades/configuración del provider LLM."""
        if provider == "openai":
            configured = bool(self.openai_api_key)
            reason = "ok" if configured else "missing_openai_api_key"
            return {
                "provider": provider,
                "supported": True,
                "configured": configured,
                "answer": True,
                "verify": True,
                "reason": reason,
            }
        if provider == "anthropic":
            configured = bool(self.anthropic_api_key)
            reason = "ok" if configured else "missing_anthropic_api_key"
            return {
                "provider": provider,
                "supported": True,
                "configured": configured,
                "answer": True,
                "verify": True,
                "reason": reason,
            }
        if provider == "gemini":
            configured = bool(self.gemini_api_key)
            reason = "ok" if configured else "missing_gemini_api_key"
            return {
                "provider": provider,
                "supported": True,
                "configured": configured,
                "answer": True,
                "verify": True,
                "reason": reason,
            }
        configured = self.is_vertex_ai_configured()
        reason = "ok" if configured else "missing_vertex_ai_api_key_or_project"
        return {
            "provider": provider,
            "supported": True,
            "configured": configured,
            "answer": True,
            "verify": True,
            "reason": reason,
        }

    def is_verify_enabled(self) -> bool:
        """Devuelve si la verificación LLM está habilitada."""
        return bool(self.llm_verify_enabled and self.openai_verify_enabled)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la instancia de configuración singleton."""
    settings = Settings()
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    return settings
