"""Configuración de la aplicación cargada desde variables de entorno."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la instancia de configuración singleton."""
    settings = Settings()
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    return settings
