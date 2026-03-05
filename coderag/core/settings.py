"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized settings for runtime configuration."""

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return singleton settings instance."""
    settings = Settings()
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    return settings
