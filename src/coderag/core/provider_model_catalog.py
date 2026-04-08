"""Catálogos fallback de modelos por provider para UI y backend."""

PROVIDER_ALIASES: dict[str, str] = {
    "vertex": "vertex_ai",
    "vertexai": "vertex_ai",
}

EMBEDDING_MODEL_OPTIONS: dict[str, list[str]] = {
    "openai": [
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    ],
    "gemini": [
        "text-embedding-004",
    ],
    "vertex_ai": [
        "text-embedding-005",
        "text-multilingual-embedding-002",
    ],
}

LLM_MODEL_OPTIONS: dict[str, list[str]] = {
    "openai": [
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4o-mini",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ],
    "vertex_ai": [
        "gemini-2.0-flash",
        "gemini-1.5-pro",
    ],
}

DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "gemini": "text-embedding-004",
    "vertex_ai": "text-embedding-005",
}

DEFAULT_LLM_MODELS: dict[str, str] = {
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-2.0-flash",
    "vertex_ai": "gemini-2.0-flash",
}


def embedding_models_for_provider(provider: str) -> list[str]:
    """Devuelve lista fallback de modelos de embeddings por provider."""
    normalized = normalize_provider_name(provider)
    return list(EMBEDDING_MODEL_OPTIONS.get(normalized, []))


def llm_models_for_provider(provider: str) -> list[str]:
    """Devuelve lista fallback de modelos LLM por provider."""
    normalized = normalize_provider_name(provider)
    return list(LLM_MODEL_OPTIONS.get(normalized, []))


def default_embedding_model(provider: str) -> str:
    """Devuelve modelo fallback recomendado para embeddings."""
    normalized = normalize_provider_name(provider)
    return DEFAULT_EMBEDDING_MODELS.get(normalized, "")


def default_llm_model(provider: str) -> str:
    """Devuelve modelo fallback recomendado para LLM."""
    normalized = normalize_provider_name(provider)
    return DEFAULT_LLM_MODELS.get(normalized, "")


def normalize_provider_name(provider: str) -> str:
    """Normaliza alias de provider hacia claves canónicas del catálogo."""
    normalized = provider.strip().lower()
    return PROVIDER_ALIASES.get(normalized, normalized)
